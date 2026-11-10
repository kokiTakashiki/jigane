"""サブモジュールの状態（`submodule_status` の型）を、利用者に見せる一件一行の文にする。

照合の表、`design suggest` の出力、方針の資料が同じ文を使う。
"""

from collections.abc import Iterable
from typing import assert_never

from jigane.design.submodule_status import (
    ConflictedSubmodule,
    DifferentCommitSubmodule,
    SubmoduleNote,
    UninitializedSubmodule,
    UnrecordedSubmodule,
)


def submodule_lines(notes: Iterable[SubmoduleNote]) -> list[str]:
    return [_line(note) for note in notes]


def _line(note: SubmoduleNote) -> str:
    match note:
        case UninitializedSubmodule():
            return (
                f"注記: サブモジュール `{note.path}` は初期化していないため、中のファイルは"
                f"照合の対象に入っていない（親が指すコミット {note.recorded}）"
            )
        case DifferentCommitSubmodule():
            return (
                f"注記: サブモジュール `{note.path}` は親が指すコミット {note.recorded} と"
                f"違うコミット {note.checked_out} を取り出していて、"
                "照合はこのコミットの中身で行った"
            )
        case ConflictedSubmodule():
            return (
                f"注記: サブモジュール `{note.path}` はマージの衝突中で、"
                "どのコミットの中身で照合したかを確かめられない"
            )
        case UnrecordedSubmodule():
            return (
                f"注記: サブモジュール `{note.path}` はコミット {note.checked_out} を"
                "取り出しているが、親の HEAD から記録のコミットを引けず、食い違いを確かめられない"
            )
        case _ as unreachable:
            assert_never(unreachable)
