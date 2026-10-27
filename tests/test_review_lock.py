import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest
from review_fakes import group_result, stored_finding

from jigane.findings.records import (
    FINDINGS_FILE,
    load_findings_state,
    merge_group,
    read_stored_state,
    reserve_numbers,
    save_findings_state,
)
from jigane.groups.definitions import MACRO, SECURITY, GroupId
from jigane.storage import store
from jigane.storage.store import LockTimeoutError, review_lock, write_text_atomic


def _run_in_threads(targets: list[Callable[[], object]]) -> None:
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _overlapping_merge(
    directory: Path, group: str, *, locked: bool, barrier: threading.Barrier | None
) -> None:
    """読んだ後、保存の前に、バリアで他方の読みを待つ保存。ロックが無ければ二つの読みは必ず重なる。"""

    def save() -> None:
        state = read_stored_state(directory)
        if barrier is not None:
            barrier.wait(timeout=10)
        state.groups[GroupId(group)] = (
            stored_finding(
                {
                    "id": group,
                    "severity": "warning",
                    "viewpoint": "V",
                    "claim": "C",
                    "triage": "must_read",
                }
            ),
        )
        save_findings_state(directory, state)

    if locked:
        with review_lock(directory):
            save()
    else:
        save()


def test_two_groups_saved_concurrently_both_remain(tmp_path: Path) -> None:
    # ロックの中ではもう一方が読めないので、バリアは使えない
    _run_in_threads(
        [
            partial(_overlapping_merge, tmp_path, g, locked=True, barrier=None)
            for g in ("macro", "security")
        ]
    )
    assert set(load_findings_state(tmp_path).groups) == {"macro", "security"}


def test_merge_group_keeps_other_groups(tmp_path: Path) -> None:
    merge_group(tmp_path, MACRO, group_result([{"id": "F1", "triage": "must_read"}]), None)
    merge_group(tmp_path, SECURITY, group_result([{"id": "F2", "triage": "must_read"}]), None)
    assert set(load_findings_state(tmp_path).groups) == {"macro", "security"}


def test_reserved_numbers_do_not_overlap(tmp_path: Path) -> None:
    firsts: list[int] = []
    _run_in_threads([lambda: firsts.append(reserve_numbers(tmp_path, 5)) for _ in range(8)])
    assert sorted(firsts) == [1 + 5 * index for index in range(8)]
    assert load_findings_state(tmp_path).last_number == 40


def test_lock_times_out(tmp_path: Path) -> None:
    sleeps: list[float] = []
    elapsed = [0.0]

    def record_sleep(seconds: float) -> None:
        # 待たずに記録し、偽の時計を進める。期限は偽の時計で測るので実時間は経たない
        sleeps.append(seconds)
        elapsed[0] += seconds

    with review_lock(tmp_path):
        with (
            pytest.raises(LockTimeoutError),
            review_lock(
                tmp_path, timeout=0.2, poll=0.05, sleep=record_sleep, clock=lambda: elapsed[0]
            ),
        ):
            pass
        assert sleeps


def test_lock_held_for_long_is_not_taken_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """時計が進んでも、生きている持ち主のロックは奪えない。"""
    with review_lock(tmp_path):
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 10_000)
        elapsed = [0.0]

        def advance(seconds: float) -> None:
            elapsed[0] += seconds

        with (
            pytest.raises(LockTimeoutError),
            review_lock(tmp_path, timeout=0.2, poll=0.01, sleep=advance, clock=lambda: elapsed[0]),
        ):
            pass


def test_lock_held_by_crashed_process_is_released(tmp_path: Path) -> None:
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, time\n"
                "from pathlib import Path\n"
                "from jigane.storage.store import review_lock\n"
                "with review_lock(Path(sys.argv[1])):\n"
                "    print('held', flush=True)\n"
                "    time.sleep(60)\n"
            ),
            str(tmp_path),
        ],
        # 子が import する jigane を、このテストを動かしている親と同じ src に固定する。
        # 別の worktree が同じ仮想環境の .pth を書き換えても、子は取り違えない
        env={**os.environ, "PYTHONPATH": str(Path(store.__file__).resolve().parents[2])},
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        elapsed = [0.0]

        def advance(seconds: float) -> None:
            elapsed[0] += seconds

        with (
            pytest.raises(LockTimeoutError),
            review_lock(tmp_path, timeout=0.2, poll=0.01, sleep=advance, clock=lambda: elapsed[0]),
        ):
            pass
        # finally を走らせずに落とす。OS が持ち主の終了でロックを外す
        holder.kill()
        holder.wait()
        # wait が返った時点で OS はロックを外している。待たずに取れるので期限は偽の時計で測る
        released_elapsed = [0.0]

        def advance_released(seconds: float) -> None:
            released_elapsed[0] += seconds

        with review_lock(
            tmp_path, timeout=2, sleep=advance_released, clock=lambda: released_elapsed[0]
        ):
            pass
    finally:
        holder.kill()
        holder.wait()
        if holder.stdout is not None:
            holder.stdout.close()


def test_lock_file_remains_after_release(tmp_path: Path) -> None:
    with review_lock(tmp_path):
        pass
    assert (tmp_path / store.LOCK_FILE).exists()


def test_lock_released_after_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), review_lock(tmp_path):
        raise RuntimeError
    with review_lock(tmp_path, timeout=0.2):
        pass


def test_write_text_atomic_uses_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, Path]] = []
    real = os.replace

    def recording_replace(src: Path, dst: Path) -> None:
        calls.append((Path(src), Path(dst)))
        real(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)
    target = tmp_path / FINDINGS_FILE
    write_text_atomic(target, json.dumps({"a": 1}))
    assert [dst for _, dst in calls] == [target]
    assert calls[0][0] != target
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == [FINDINGS_FILE]


def test_write_text_atomic_failure_keeps_old_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "x.json"
    target.write_text("old", encoding="utf-8")

    def fail(_src: object, _dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]
