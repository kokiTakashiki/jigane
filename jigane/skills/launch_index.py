"""方針の起動の種類と、runs.json に保存する起動の通し番号との変換。

保存する番号は、図の起動が 0 で、原則の起動は 1 から。この値を知るのはこのモジュールだけで、
書く側（policy_launch.stored_index）も、読み戻す側（kind_of）も、ここを通る。
"""

from enum import StrEnum
from typing import assert_never

from jigane.groups.policy_runs import PolicyRole


class LaunchKind(StrEnum):
    """方針の起動の種類。値は PolicyRole の同じ種類の値で、役の語を一つにする。

    PolicyRole は設計書の形や廃止した形の役も持つため、網羅を確かめる match はこの型で行う。
    """

    DIAGRAM = PolicyRole.DIAGRAM.value
    PRINCIPLE = PolicyRole.PRINCIPLE.value


def role_of_kind(kind: LaunchKind) -> PolicyRole:
    """起動の種類に対応する、runs.json の起動の role。対応はここだけで定義する。"""
    match kind:
        case LaunchKind.DIAGRAM:
            return PolicyRole.DIAGRAM
        case LaunchKind.PRINCIPLE:
            return PolicyRole.PRINCIPLE
        case _:
            assert_never(kind)


_DIAGRAM_INDEX = 0


def to_index(kind: LaunchKind, ordinal: int) -> int:
    """書く変換。ordinal は原則の起動の並びの位置（1 から）で、図の起動では読まない。"""
    match kind:
        case LaunchKind.DIAGRAM:
            return _DIAGRAM_INDEX
        case LaunchKind.PRINCIPLE:
            return ordinal
        case _:
            assert_never(kind)


def kind_of(index: int | None) -> LaunchKind | None:
    """読み戻す変換。番号を持たない記録（None）と、負の番号（壊れた記録）は種類が不明で、None。"""
    if index is None or index < _DIAGRAM_INDEX:
        return None
    return LaunchKind.DIAGRAM if index == _DIAGRAM_INDEX else LaunchKind.PRINCIPLE
