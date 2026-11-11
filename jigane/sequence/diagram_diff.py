"""変更前と変更後の矢印の列を突き合わせ、対比の印を付ける（4.2.4 項）。"""

import itertools
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jigane.gates.tree_side import TreeSide


def _arrow_signature(arrow: Mapping[str, Any]) -> tuple[str, str, str]:
    return arrow["sender"], arrow["receiver"], arrow["name"]


class PlainMark(StrEnum):
    """対の受け手を持たない対比の印。値の文字列がそのまま JSON の mark になる。"""

    SAME = "same"
    ADDED = "added"
    REMOVED = "removed"


@dataclass(frozen=True)
class Retargeted:
    """呼び先だけを替えた削除と追加の組。対の受け手と側は、この印だけが持つ。"""

    paired_receiver: str
    paired_side: TreeSide


ArrowMark = PlainMark | Retargeted


def mark_differences(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    """変更前と変更後の矢印の列を最長共通部分列で突き合わせ、対比の印 mark を付ける（4.2.4 項）。

    組は（送り手、受け手、呼び出しの名前）で、行番号は使わない。突き合わせの後に、
    呼び先だけを替えた削除と追加の組を付け替え（retargeted）にする。
    """
    old = [_arrow_signature(a) for a in before]
    new = [_arrow_signature(a) for a in after]
    # lengths[i][j] は old[i:] と new[j:] の最長共通部分列の長さ
    lengths = [[0] * (len(new) + 1) for _ in range(len(old) + 1)]
    for i in range(len(old) - 1, -1, -1):
        for j in range(len(new) - 1, -1, -1):
            lengths[i][j] = (
                lengths[i + 1][j + 1] + 1
                if old[i] == new[j]
                else max(lengths[i + 1][j], lengths[i][j + 1])
            )
    for arrow in before:
        arrow["mark"] = PlainMark.REMOVED
    for arrow in after:
        arrow["mark"] = PlainMark.ADDED
    # 一致した組の位置。付け替えの突き合わせの区間の境になる
    anchors: list[tuple[int, int]] = []
    i = j = 0
    while i < len(old) and j < len(new):
        if old[i] == new[j]:
            before[i]["mark"] = after[j]["mark"] = PlainMark.SAME
            anchors.append((i, j))
            i += 1
            j += 1
        elif lengths[i + 1][j] >= lengths[i][j + 1]:
            i += 1
        else:
            j += 1
    # 先頭の区間と末尾の区間も含めるため、両端に番兵を置く
    bounds = [(-1, -1), *anchors, (len(old), len(new))]
    for (i0, j0), (i1, j1) in itertools.pairwise(bounds):
        for gone, arrived in _pair_retargeted(before[i0 + 1 : i1], after[j0 + 1 : j1]):
            gone["mark"] = Retargeted(arrived["receiver"], TreeSide.AFTER)
            arrived["mark"] = Retargeted(gone["receiver"], TreeSide.BEFORE)


def _pair_retargeted(
    removed_span: list[dict[str, Any]], added_span: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """一つの区間の削除と追加を前から順に見て、送り手と名前が同じで受け手だけ違う組を返す。"""
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    taken: set[int] = set()
    for gone in removed_span:
        for index, arrived in enumerate(added_span):
            if index in taken:
                continue  # 追加の矢印は一つの対にしか入らない
            if (
                arrived["sender"] == gone["sender"]
                and arrived["name"] == gone["name"]
                and arrived["receiver"] != gone["receiver"]
            ):
                taken.add(index)
                pairs.append((gone, arrived))
                break
    return pairs
