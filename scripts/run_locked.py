"""引数のコマンドを、マシン全体で一つの flock の錠を取ってから走らせる。

tests/gate_slots.py が import の時に pytest を読むため、
uv run の環境（dev の依存を入れた環境）で動かす。
implementer の終わりのゲートが同時に何本も走ると機械が混むので、
claude.gateCommand の先頭に置いて一本ずつにする。
macOS には flock のコマンドが無いため、fcntl.flock をここで呼ぶ。
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import IO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

import gate_slots

if sys.platform == "win32":
    # Windows には fcntl が無いので、錠を取らずそのまま走らせる（gate_slots と同じ扱い）
    pass
else:
    import fcntl

LOCK_NAME = "gate-command.lock"
DIRECTORY_VARIABLE = "JIGANE_GATE_LOCK_DIR"
WIN32_MESSAGE = "Windows には錠が無い"


def lock_directory() -> Path:
    override = os.environ.get(DIRECTORY_VARIABLE)
    if override:
        return Path(override)
    # 席と同じ置き場。席のファイルは slot-N なので名前は重ならない
    return gate_slots.default_slot_directory()


def acquire_lock(directory: Path) -> IO[bytes]:
    """錠を取るまで待つ。待ち始めに一行だけ標準エラーへ出す。プロセスが消えれば OS が外す。"""
    if sys.platform != "win32":
        directory.mkdir(parents=True, exist_ok=True)
        handle = (directory / LOCK_NAME).open("ab")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                f"ゲートの錠を待っています: {directory / LOCK_NAME}", file=sys.stderr, flush=True
            )
            fcntl.flock(handle, fcntl.LOCK_EX)
        return handle
    # main は Windows では呼ばない。fcntl の無い環境の型検査に、上の枝を除かせるための分岐
    raise RuntimeError(WIN32_MESSAGE)


def run_command(command: list[str]) -> int:
    # 出力は受け止めずそのまま流す
    return subprocess.run(command, check=False).returncode


def main(command: list[str]) -> int:
    if not command:
        print("使い方: run_locked.py <コマンド> [引数...]", file=sys.stderr)
        return 2
    if sys.platform == "win32":
        # 錠を取らない。理由は import の分岐と acquire_lock の末尾の分岐に書いた
        return run_command(command)
    # 開いたままの fd が錠を持つ。子へは close_fds で渡らないので、子だけが残っても錠は放される
    with acquire_lock(lock_directory()):
        return run_command(command)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
