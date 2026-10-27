"""構文木を一度歩いて、規則 R1〜R7 の元になる値を数える。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from jigane.regex.syntax import (
    Alternation,
    Atom,
    AtomKind,
    Backreference,
    CaptureGroup,
    CharClass,
    Conditional,
    FlaggedGroup,
    Group,
    LeadingFlags,
    Look,
    Quantifier,
    Quote,
    RegexNode,
    Sequence,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_BRANCH_EDGE_COUNT = 2


class CatastrophicForm(StrEnum):
    NESTED_QUANTIFIER = "nested_quantifier"
    OVERLAPPING_ALTERNATION = "overlapping_alternation"


@dataclass(frozen=True)
class RegexMetrics:
    # R1: S5843 の重み
    complexity: int
    # R2: 一つの選択の中の選択肢の最大の数（選択が無ければ 0）と、選択が別の選択の中にあるか
    max_branches: int
    nested_alternation: bool
    # R3
    lookaround_count: int
    has_conditional: bool
    # R4
    max_nesting: int
    # R5
    unnamed_captures: int
    # R6 に当たる、本文が特殊な文字を持たない並びかどうか
    no_special_chars: bool
    # R7: 当たった形。重複は除き、出現順に並べる
    catastrophic_forms: tuple[CatastrophicForm, ...]


class _Counter:
    def __init__(self) -> None:
        self.complexity = 0
        self.max_branches = 0
        self.nested_alternation = False
        self.lookaround_count = 0
        self.has_conditional = False
        self.max_nesting = 0
        self.unnamed_captures = 0
        self.forms: list[CatastrophicForm] = []

    def walk(self, node: RegexNode, depth: int, nesting: int, *, in_branch: bool) -> None:
        """depth は S5843 の入れ子（外側が 1）、nesting は R4 の段数（量指定子付きの組は一段）。"""
        if isinstance(node, Sequence):
            for item in node.items:
                self.walk(item, depth, nesting, in_branch=in_branch)
        elif isinstance(node, Alternation):
            self._walk_alternation(node, depth, nesting, in_branch=in_branch)
        elif isinstance(node, Quantifier):
            self._walk_quantifier(node, depth, nesting, in_branch=in_branch)
        elif isinstance(node, Group):
            self._walk_group(node, depth, nesting, in_branch=in_branch)
        elif isinstance(node, Look):
            self._walk_look(node, depth, nesting, in_branch=in_branch)
        elif isinstance(node, Conditional):
            self._walk_conditional(node, depth, nesting, in_branch=in_branch)
        elif isinstance(node, CharClass | Backreference):
            self.complexity += 1
        elif isinstance(node, Atom | Quote | LeadingFlags):
            # 単独の既定のクラス（\d など）や一文字は S5843 で 0 点
            pass

    def _walk_alternation(
        self, node: Alternation, depth: int, nesting: int, *, in_branch: bool
    ) -> None:
        self.max_branches = max(self.max_branches, len(node.branches))
        if in_branch:
            self.nested_alternation = True
        self.complexity += depth + len(node.branches) - 1
        for branch in node.branches:
            self.walk(branch, depth + 1, nesting, in_branch=True)

    def _walk_quantifier(
        self, node: Quantifier, depth: int, nesting: int, *, in_branch: bool
    ) -> None:
        self.complexity += depth
        self._check_forms(node)
        if isinstance(node.target, Group):
            # 量指定子の付いた組は一段。組の側で数えるので、ここでは段を足さない
            self.walk(node.target, depth + 1, nesting, in_branch=in_branch)
        else:
            self.max_nesting = max(self.max_nesting, nesting + 1)
            self.walk(node.target, depth + 1, nesting + 1, in_branch=in_branch)

    def _walk_group(self, node: Group, depth: int, nesting: int, *, in_branch: bool) -> None:
        self.max_nesting = max(self.max_nesting, nesting + 1)
        if isinstance(node, CaptureGroup):
            self.unnamed_captures += 1
        flagged = isinstance(node, FlaggedGroup)
        if flagged:
            self.complexity += depth
        self.walk(node.body, depth + 1 if flagged else depth, nesting + 1, in_branch=in_branch)

    def _walk_look(self, node: Look, depth: int, nesting: int, *, in_branch: bool) -> None:
        self.lookaround_count += 1
        self.complexity += depth
        self.max_nesting = max(self.max_nesting, nesting + 1)
        self.walk(node.body, depth + 1, nesting + 1, in_branch=in_branch)

    def _walk_conditional(
        self, node: Conditional, depth: int, nesting: int, *, in_branch: bool
    ) -> None:
        self.has_conditional = True
        self.complexity += depth
        self.max_nesting = max(self.max_nesting, nesting + 1)
        self.walk(node.yes, depth + 1, nesting + 1, in_branch=in_branch)
        if node.no is not None:
            self.walk(node.no, depth + 1, nesting + 1, in_branch=in_branch)

    def _check_forms(self, node: Quantifier) -> None:
        if node.maximum is not None:
            # 上限のある繰り返しは、一致しない入力でも試す回数が有限で爆発しない
            return
        if (
            isinstance(node.target, Group)
            and _has_repeating_quantifier(node.target.body)
            and not _has_mandatory_single(node.target.body)
            and not _has_disjoint_separator(node.target.body)
        ):
            self._add(CatastrophicForm.NESTED_QUANTIFIER)
        target = _unwrap_groups(node.target)
        if isinstance(target, Alternation) and _has_overlapping_start(target):
            self._add(CatastrophicForm.OVERLAPPING_ALTERNATION)

    def _add(self, form: CatastrophicForm) -> None:
        if form not in self.forms:
            self.forms.append(form)


def _unwrap_groups(node: RegexNode) -> RegexNode:
    """量指定子の対象から組の包みを剥いで中身を返す。"""
    while isinstance(node, Group):
        node = node.body
    return node


def _children(node: RegexNode) -> tuple[RegexNode, ...]:
    if isinstance(node, Sequence):
        return node.items
    if isinstance(node, Alternation):
        return node.branches
    if isinstance(node, Group | Look):
        return (node.body,)
    if isinstance(node, Quantifier):
        return (node.target,)
    if isinstance(node, Conditional):
        return (node.yes,) if node.no is None else (node.yes, node.no)
    return ()


def _has_repeating_quantifier(node: RegexNode) -> bool:
    """二回以上繰り返せる量指定子が、本文のどこかにあるか。"""
    if isinstance(node, Quantifier) and (node.maximum is None or node.maximum > 1):
        return True
    return any(_has_repeating_quantifier(child) for child in _children(node))


def _has_mandatory_single(node: RegexNode) -> bool:
    """本文に必ず一度だけ現れる一文字（量指定子の付かない CHAR か ESCAPE）があるか。

    選択や量指定子や先読みの中は「必ず」と言えないので見ない。
    """
    if isinstance(node, Atom):
        return node.kind in (AtomKind.CHAR, AtomKind.ESCAPE)
    if isinstance(node, Sequence):
        return any(_has_mandatory_single(item) for item in node.items)
    if isinstance(node, Group):
        return _has_mandatory_single(node.body)
    return False


def _edge_atoms(node: RegexNode, *, last: bool) -> list[Atom] | None:
    """要素の先頭（last が偽）か末尾の文字。選択肢ごとに一つ集める。決められなければ None。"""
    if isinstance(node, Atom):
        return [node]
    if isinstance(node, Sequence):
        return _edge_atoms(node.items[-1 if last else 0], last=last) if node.items else None
    if isinstance(node, Group):
        return _edge_atoms(node.body, last=last)
    if isinstance(node, Quantifier):
        return _edge_atoms(node.target, last=last)
    if isinstance(node, Alternation):
        return _alternation_edge_atoms(node, last=last)
    # 文字クラスは中身を持たないので、重ならないと言えない
    return None


def _alternation_edge_atoms(node: Alternation, *, last: bool) -> list[Atom] | None:
    """選択肢ごとの先頭か末尾の文字を集める。一つでも決められなければ None。"""
    found: list[Atom] = []
    for branch in node.branches:
        atoms = _edge_atoms(branch, last=last)
        if atoms is None:
            return None
        found += atoms
    return found


_CLASS_MEMBERS: dict[str, Callable[[str], bool]] = {
    r"\s": lambda char: char.isspace(),
    r"\d": lambda char: char.isdigit(),
    r"\w": lambda char: char.isalnum() or char == "_",
}


def _may_overlap(left: Atom, right: Atom) -> bool:
    """二つの一文字が同じ文字に当たりうるか。分からない書き方は当たりうるとする。"""
    for first, second in ((left, right), (right, left)):
        if first.kind is AtomKind.CHAR and second.kind is AtomKind.CHAR:
            return first.text == second.text
        member = _CLASS_MEMBERS.get(second.text) if second.kind is AtomKind.CLASS else None
        if first.kind is AtomKind.CHAR and member is not None:
            return member(first.text)
    both = {left.text, right.text}
    both_known = left.kind is AtomKind.CLASS and right.kind is AtomKind.CLASS
    if both_known and both <= _CLASS_MEMBERS.keys():
        # \s は \d とも \w とも重ならない。\d と \w は重なる
        return len(both) == 1 or both == {r"\d", r"\w"}
    return True


def _has_disjoint_separator(node: RegexNode) -> bool:
    """本文の並びに、直前の要素と文字が重ならない区切り（一回以上の量指定子の付いた一文字）があるか。

    区切りが省ける（`?` や `*`）形は、切れ目が一通りに決まらないので含めない。
    """
    if isinstance(node, Group):
        return _has_disjoint_separator(node.body)
    if not isinstance(node, Sequence):
        return False
    for previous, item in zip(node.items, node.items[1:], strict=False):
        if not (
            isinstance(item, Quantifier)
            and item.minimum >= 1
            and isinstance(item.target, Atom)
            and item.target.kind in (AtomKind.CLASS, AtomKind.CHAR, AtomKind.ESCAPE)
        ):
            continue
        edges = [
            atoms
            for atoms in (_edge_atoms(previous, last=False), _edge_atoms(previous, last=True))
            if atoms is not None
        ]
        if len(edges) == _BRANCH_EDGE_COUNT and not any(
            _may_overlap(item.target, atom) for atoms in edges for atom in atoms
        ):
            return True
    return False


def _first_char(node: RegexNode) -> str | None:
    """選択肢が必ず最初に読む CHAR。決められなければ None。"""
    if isinstance(node, Sequence):
        return _first_char(node.items[0]) if node.items else None
    if isinstance(node, Atom):
        return node.text if node.kind is AtomKind.CHAR else None
    if isinstance(node, Quantifier):
        return _first_char(node.target) if node.minimum >= 1 else None
    if isinstance(node, Group):
        return _first_char(node.body)
    return None


def _has_overlapping_start(node: Alternation) -> bool:
    seen: set[str] = set()
    for branch in node.branches:
        first = _first_char(branch)
        if first is None:
            continue
        if first in seen:
            return True
        seen.add(first)
    return False


def _is_literal_only(node: RegexNode) -> bool:
    literal_kinds = (AtomKind.CHAR, AtomKind.ESCAPE)
    if isinstance(node, Atom):
        return node.kind in literal_kinds
    if isinstance(node, Sequence):
        return bool(node.items) and all(
            isinstance(item, Atom) and item.kind in literal_kinds for item in node.items
        )
    return False


def measure(node: RegexNode) -> RegexMetrics:
    counter = _Counter()
    counter.walk(node, depth=1, nesting=0, in_branch=False)
    return RegexMetrics(
        complexity=counter.complexity,
        max_branches=counter.max_branches,
        nested_alternation=counter.nested_alternation,
        lookaround_count=counter.lookaround_count,
        has_conditional=counter.has_conditional,
        max_nesting=counter.max_nesting,
        unnamed_captures=counter.unnamed_captures,
        no_special_chars=_is_literal_only(node),
        catastrophic_forms=tuple(counter.forms),
    )
