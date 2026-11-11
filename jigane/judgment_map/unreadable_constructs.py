"""読めない記述の検出。"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import Path

from tree_sitter import Node

from jigane.gates.tree_side import TreeSide
from jigane.judgment_map.classification import Segment
from jigane.judgment_map.kinds import Kind
from jigane.judgment_map.tree_parsing import read_and_parse
from jigane.languages import registry
from jigane.languages.csharp_nodes import nameless_declarator_nodes
from jigane.languages.swift_nodes import operator_function_nodes
from jigane.languages.value_set_core import walk_nodes

# 該当行の本文を残す長さ。正規表現の札の EXCERPT_LIMIT と同じ
EXCERPT_LIMIT = 200


class ConstructKind(Enum):
    """読めない記述の種類。"""

    SYNTAX_ERROR = "syntax_error"
    SWIFT_OPERATOR_FUNCTION = "swift_operator_function"
    CSHARP_NAMELESS_DECLARATOR = "csharp_nameless_declarator"


@dataclass(frozen=True)
class Occurrence:
    kind: ConstructKind
    path: str
    line: int
    side: TreeSide
    excerpt: str


@dataclass(frozen=True)
class UnreadableConstructs:
    occurrences: tuple[Occurrence, ...] = ()

    def of_kind(self, kind: ConstructKind) -> tuple[Occurrence, ...]:
        return tuple(o for o in self.occurrences if o.kind is kind)


# 言語の名前 -> 種類と、その言語の木から記述の節点を探す関数。
# 構文エラーは言語を問わないので載せない
_FINDERS: dict[str, tuple[ConstructKind, Callable[[Node], Iterator[Node]]]] = {
    "swift": (ConstructKind.SWIFT_OPERATOR_FUNCTION, operator_function_nodes),
    "csharp": (ConstructKind.CSHARP_NAMELESS_DECLARATOR, nameless_declarator_nodes),
}


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


def _scan(
    source: bytes, root: Node, language: str, path: str, side: TreeSide
) -> Iterator[Occurrence]:
    excerpt = _excerpt_reader(source)
    if root.has_error:
        # 構文エラーのファイルに他の規則を当てると、壊れた木の宣言が同じ行を二重に示す
        first = next((n for n in walk_nodes(root) if n.type == "ERROR" or n.is_missing), root)
        line = _line_of(first)
        yield Occurrence(ConstructKind.SYNTAX_ERROR, path, line, side, excerpt(line))
        return
    finder = _FINDERS.get(language)
    if finder is None:
        # 規則を持たない言語は、構文エラーの規則だけで足りる
        return
    kind, nodes_of = finder
    for node in nodes_of(root):
        line = _line_of(node)
        yield Occurrence(kind, path, line, side, excerpt(line))


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
            found.extend(_scan(parsed[0], parsed[1].root_node, adapter.name, path, side))
    return UnreadableConstructs(tuple(found))
