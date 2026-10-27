"""マシン全体のゲートの並列を絞る pytest の差し込み。

実行役と implementer の作業ツリーでゲートが同時に走ると、python の作業者が
マシンのコア数を超えて起き、負荷が跳ね上がる。席のファイルの flock で同時に走る
pytest を SLOT_COUNT 本までにし、作業者の数は席の数で割ったコア数にする。
"""

import os
import sys
import threading
from pathlib import Path
from typing import IO, NoReturn

import pytest

if sys.platform == "win32":
    # Windows には fcntl が無いので、席を取らず並列の絞りも持たない。
    # Windows の CI のランナーは一つのジョブを一つの VM で走らせるので、マシン全体の絞りは要らない
    pass
else:
    import fcntl

SLOT_COUNT = 2
POLL_SECONDS = 0.5
WAIT_MESSAGE = f"ゲートの席が空くのを待っている（同時に {SLOT_COUNT} 本まで）"

_held: list[IO[bytes]] = []


def default_slot_directory() -> Path:
    return Path.home() / ".cache" / "jigane-gate"


def workers_per_slot(cpu_count: int | None, slot_count: int = SLOT_COUNT) -> int:
    # cpu_count は数えられないとき None を返す
    return max(1, (cpu_count or 1) // slot_count)


def _refuse_on_windows() -> NoReturn:
    # raise を if の腕に直接書くと、ruff が続く else を余計と見て外させるため、関数に出す
    raise RuntimeError("席のロックは Windows では使えません")


def try_acquire_slot(directory: Path, slot_count: int = SLOT_COUNT) -> IO[bytes] | None:
    """席を順に非待ちで取りにいき、取れた席の fd を返す。全席が埋まっていれば None。"""
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # pytest_configure が Windows では席を取らずに戻るので、ここへは来ない前提
        _refuse_on_windows()
    else:
        for index in range(slot_count):
            handle = (directory / f"slot-{index}").open("ab")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                continue
            return handle
        return None


def acquire_slot(
    directory: Path,
    slot_count: int = SLOT_COUNT,
    poll_seconds: float = POLL_SECONDS,
    stream: IO[str] | None = None,
) -> IO[bytes]:
    """どれかの席が空くまで待って取る。待ち始めに一行だけ出す。"""
    handle = try_acquire_slot(directory, slot_count)
    if handle is not None:
        return handle
    print(WAIT_MESSAGE, file=stream or sys.stderr, flush=True)
    while True:
        # 席の解放は通知されないため、一定の間隔で取り直す。
        threading.Event().wait(poll_seconds)
        handle = try_acquire_slot(directory, slot_count)
        if handle is not None:
            return handle


def pytest_configure(config: pytest.Config) -> None:
    # 作業者は制御の側の席の下で走るので、作業者の側では取らない
    if sys.platform == "win32":
        # 席を取らない。理由は import の分岐に書いた
        return
    if hasattr(config, "workerinput"):
        return
    _held.append(acquire_slot(default_slot_directory()))


def pytest_unconfigure() -> None:
    # fd を閉じると flock も放される
    while _held:
        _held.pop().close()


def pytest_xdist_auto_num_workers() -> int:
    return workers_per_slot(os.cpu_count())
