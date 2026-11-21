"""Codex P3 に渡すシグネチャを、変更前後に戻せる形で描く。"""

import re
from collections.abc import Callable, Sequence
from difflib import SequenceMatcher

from jigane.figures.models import (
    AddedSignature,
    ChangedSignature,
    DeletedSignature,
    MovedSignature,
    SignatureEntry,
)
from jigane.markdown.fence import fence

_TOKENS = re.compile(r"\w+|\s+|[^\w\s]")
_MARKS = "⟦¦⟧"
_LEGEND = (
    "+ は宣言の追加、- は宣言の削除、~ は同じ宣言の前後。"
    "⟦旧¦新⟧ ごとに左か右を選ぶと、共通部分と合わせて各側の全文になる。"
    "囲みの空欄はその側に当該の文字列がないことを表す。"
)


def _paired_text(entry: ChangedSignature) -> str:
    old, new = entry.old, entry.new
    separate = f"~ 旧 {old}\n  新 {new}"
    if any(mark in old or mark in new for mark in _MARKS):
        return separate
    before, after = _TOKENS.findall(old), _TOKENS.findall(new)
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    pieces = []
    for tag, first, last, start, end in matcher.get_opcodes():
        left = "".join(before[first:last])
        if tag == "equal":
            pieces.append(left)
        else:
            pieces.append(f"⟦{left}¦{''.join(after[start:end])}⟧")
    paired = "~ " + "".join(pieces)
    return paired if len(paired.encode()) < len(separate.encode()) else separate


def _entry_text(entry: SignatureEntry, place: Callable[[str], str]) -> str:
    if isinstance(entry, MovedSignature):
        return f"{place(entry.old_place)} → {place(entry.new_place)} {entry.text}"
    position = place(entry.place)
    if isinstance(entry, AddedSignature):
        return f"{position} + {entry.new}"
    if isinstance(entry, DeletedSignature):
        return f"{position} - {entry.old}"
    return f"{position} {_paired_text(entry)}"


def comparison_text(entries: Sequence[SignatureEntry], place: Callable[[str], str]) -> str:
    """宣言を省かずに並べ、離れた差もそれぞれ共通部分の間に示す。"""
    if not entries:
        return ""
    lines = "\n".join(_entry_text(entry, place) for entry in entries)
    return f"{_LEGEND}\n{fence(lines)}"
