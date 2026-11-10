"""親の `git submodule status` から、照合の読み先に穴や食い違いがあるサブモジュールを挙げる。

状態ごとの型と、JSON に出す状態の名前（`state`）をここで一か所に持つ。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from jigane.git import process

RecordedCommit = Callable[[str], str | None]

# 印は - 未初期化、+ 記録と違うコミット、U 衝突、空白 一致
_STATUS_LINE = re.compile(r"^([-+U ])([0-9a-f]+) (.+?)(?: \([^()]*\))?$")


@dataclass(frozen=True)
class UninitializedSubmodule:
    """初期化していないサブモジュール。中のファイルは照合の対象に入らない。"""

    state: ClassVar[str] = "uninitialized"
    path: str
    # 親の HEAD が指すコミット
    recorded: str

    @property
    def checked_out(self) -> None:
        return None


@dataclass(frozen=True)
class DifferentCommitSubmodule:
    """親の HEAD が指すのと違うコミットを取り出したサブモジュール。"""

    state: ClassVar[str] = "different_commit"
    path: str
    recorded: str
    checked_out: str


@dataclass(frozen=True)
class ConflictedSubmodule:
    """マージの衝突中で、取り出すコミットが定まらないサブモジュール。"""

    state: ClassVar[str] = "conflicted"
    path: str

    @property
    def checked_out(self) -> None:
        return None

    @property
    def recorded(self) -> None:
        return None


@dataclass(frozen=True)
class UnrecordedSubmodule:
    """取り出したコミットはあるが、親の HEAD から記録のコミットを引けないサブモジュール。"""

    state: ClassVar[str] = "unrecorded"
    path: str
    checked_out: str

    @property
    def recorded(self) -> None:
        return None


SubmoduleNote = (
    UninitializedSubmodule | DifferentCommitSubmodule | ConflictedSubmodule | UnrecordedSubmodule
)


def classify_status(status: str, recorded_commit: RecordedCommit) -> tuple[SubmoduleNote, ...]:
    """`git submodule status` の出力を、印が - U + のサブモジュールの注記にする（純粋）。

    `recorded_commit` は、+ のサブモジュールのパスから親の HEAD が記録するコミットを返す。
    引けなければ None。
    """
    notes: list[SubmoduleNote] = []
    for line in status.splitlines():
        parsed = _STATUS_LINE.match(line)
        if parsed is None:
            continue
        mark, commit, path = parsed.groups()
        if mark == "-":
            notes.append(UninitializedSubmodule(path, commit))
        elif mark == "U":
            notes.append(ConflictedSubmodule(path))
        elif mark == "+":
            recorded = recorded_commit(path)
            if recorded is None:
                # 記録のコミットを引けず、食い違いは言えない。確かめられない旨を注記する
                notes.append(UnrecordedSubmodule(path, commit))
            else:
                notes.append(DifferentCommitSubmodule(path, recorded, commit))
        else:
            # 印が空白は、取り出しが記録と一致している。注記する相手が無い
            continue
    return tuple(notes)


def submodule_notes(root: Path) -> tuple[SubmoduleNote, ...]:
    """git を実行して `root` のサブモジュールの注記を読む。"""
    try:
        status = process.run_command(["git", "submodule", "status"], cwd=root)
    except (OSError, process.CommandError):
        # git の外のディレクトリや git が無い環境には、サブモジュールの状態が無い
        return ()

    def recorded_commit(path: str) -> str | None:
        try:
            return process.run_command(["git", "rev-parse", f"HEAD:{path}"], cwd=root).strip()
        except (OSError, process.CommandError):
            # 親の HEAD にそのパスの記録が無い
            return None

    return classify_status(status, recorded_commit)
