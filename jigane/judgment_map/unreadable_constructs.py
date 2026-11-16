"""読めない記述の検出。"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import NamedTuple

from tree_sitter import Node

from jigane.gates.construct_kind import ConstructKind
from jigane.gates.tree_side import TreeSide
from jigane.judgment_map.classification import Segment
from jigane.judgment_map.kinds import Kind
from jigane.judgment_map.tree_parsing import read_and_parse
from jigane.judgment_map.unreadable_wording import SYNTAX_ERROR_WORDING, UnreadableWording
from jigane.languages import registry
from jigane.languages.base import (
    LanguageAdapter,
    UnsupportedSyntax,
    UnsupportedSyntaxDetector,
)
from jigane.languages.value_set_core import walk_nodes
from jigane.text.terminal import one_line_text

# 該当行の本文を残す長さ。正規表現の札の EXCERPT_LIMIT と同じ
EXCERPT_LIMIT = 200


@dataclass(frozen=True)
class Occurrence:
    kind: ConstructKind
    path: str
    line: int
    side: TreeSide
    excerpt: str
    wording: UnreadableWording


@dataclass(frozen=True)
class UnreadableConstructs:
    occurrences: tuple[Occurrence, ...] = ()

    def of_kind(self, kind: ConstructKind) -> tuple[Occurrence, ...]:
        return tuple(o for o in self.occurrences if o.kind == kind)


def _line_of(node: Node) -> int:
    return node.start_point.row + 1


def _excerpt_reader(source: bytes) -> Callable[[int], str]:
    """該当行の本文を返す関数。行への分割は最初の該当のときに一度だけ行い、以後は共有する。"""

    @cache
    def rows() -> list[str]:
        return source.decode("utf-8", errors="replace").split("\n")

    def excerpt(line: int) -> str:
        return rows()[line - 1].strip()[:EXCERPT_LIMIT]

    return excerpt


def _wording_of(adapter: LanguageAdapter, found: UnsupportedSyntax) -> UnreadableWording:
    """プラグインの値を信用せず、文面を端末へ出せる形にして空でないことを確かめる。"""
    wording = UnreadableWording(
        one_line_text(found.kind.title), one_line_text(found.kind.fix_hint)
    )
    if not wording.title or not wording.fix_hint:
        raise ValueError(
            f"言語 {adapter.name!r} の unsupported_syntax が空の見出しか直し方を返しました:"
            f" {found.kind.name!r}"
        )
    return wording


class _Found(NamedTuple):
    kind: ConstructKind
    wording: UnreadableWording
    line: int


def _checked(adapter: LanguageAdapter, returned: object, line_count: int) -> list[_Found]:
    """アダプタの戻り値を検査し、種類の値と文面と行に直す。

    誤りは型の誤りも含めて、アダプタの name を含む ValueError にする。
    """
    name = adapter.name
    if not isinstance(returned, list):
        raise ValueError(  # noqa: TRY004 プラグインの戻り値の誤りは型の誤りも ValueError にそろえる
            f"言語 {name!r} の unsupported_syntax が list でない値を返しました:"
            f" {type(returned).__name__}"
        )
    result: list[_Found] = []
    wordings: dict[str, UnreadableWording] = {}
    for found in returned:
        if not isinstance(found, UnsupportedSyntax):
            raise ValueError(  # noqa: TRY004 プラグインの戻り値の誤りは型の誤りも ValueError にそろえる
                f"言語 {name!r} の unsupported_syntax が UnsupportedSyntax でない要素を返しました:"
                f" {type(found).__name__}"
            )
        line = found.line
        # bool は int の部分型だが行ではない
        if not isinstance(line, int) or isinstance(line, bool) or not 1 <= line <= line_count:
            raise ValueError(
                f"言語 {name!r} の unsupported_syntax が範囲外の行を返しました:"
                f" {line!r}（1 から {line_count}）"
            )
        for text in (found.kind.title, found.kind.fix_hint):
            if not isinstance(text, str):
                raise ValueError(  # noqa: TRY004 プラグインの戻り値の誤りは型の誤りも ValueError にそろえる
                    f"言語 {name!r} の見出しと直し方は文字列でなければなりません:"
                    f" {found.kind.name!r}"
                )
        wording = _wording_of(adapter, found)
        if wordings.setdefault(found.kind.name, wording) != wording:
            raise ValueError(
                f"言語 {name!r} の種類 {found.kind.name!r} が、見出しか直し方の食い違う件を"
                "返しました"
            )
        try:
            kind = ConstructKind.of_language(name, found.kind.name)
        except ValueError as error:
            raise ValueError(f"言語 {name!r} の種類が不正です: {error}") from error
        result.append(_Found(kind, wording, line))
    return result


def _scan(
    source: bytes, root: Node, adapter: LanguageAdapter, path: str, side: TreeSide
) -> Iterator[Occurrence]:
    excerpt = _excerpt_reader(source)
    if root.has_error:
        # 構文エラーのファイルに他の規則を当てると、壊れた木の宣言が同じ行を二重に示す
        first = next((n for n in walk_nodes(root) if n.type == "ERROR" or n.is_missing), root)
        line = _line_of(first)
        yield Occurrence(
            ConstructKind.SYNTAX_ERROR, path, line, side, excerpt(line), SYNTAX_ERROR_WORDING
        )
        return
    if not isinstance(adapter, UnsupportedSyntaxDetector):
        # インターフェースを実装しない言語は、申告するサポートしない構文が無いことを表す
        return
    # 改行で割った行数。excerpt の行の数え方と同じ
    line_count = source.count(b"\n") + 1
    returned = adapter.unsupported_syntax(root, source)
    for checked in sorted(
        _checked(adapter, returned, line_count), key=lambda checked: checked.line
    ):
        yield Occurrence(
            checked.kind, path, checked.line, side, excerpt(checked.line), checked.wording
        )


def unreadable_constructs(
    segments: Sequence[Segment], root: Path, base_root: Path | None
) -> UnreadableConstructs:
    """区間のファイルから、読めない記述を検出する。base_root は差分のレビューだけが持つ。"""
    found: list[Occurrence] = []
    seen: set[tuple[str, TreeSide]] = set()
    sides: tuple[tuple[Path | None, TreeSide], ...] = (
        (root, TreeSide.AFTER),
        (base_root, TreeSide.BEFORE),
    )
    for segment in segments:
        if segment.kind is Kind.GENERATED:
            continue
        for (tree, side), path in zip(sides, (segment.new_path, segment.old_path), strict=True):
            if tree is None or path is None or (path, side) in seen:
                continue
            seen.add((path, side))
            adapter = registry.parsing_adapter_for(path)
            parsed = read_and_parse(tree, path, adapter) if adapter is not None else None
            if adapter is None or parsed is None:
                # アダプタが無いか、木の外へ出るリンクなど読めないファイルは検出しない
                continue
            found.extend(_scan(parsed[0], parsed[1].root_node, adapter, path, side))
    return UnreadableConstructs(tuple(found))
