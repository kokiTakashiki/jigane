"""ファイルの名前の組み立てのうち、言語に依らない部分。

言語ごとの部品（`swift_file_paths`、`java_file_paths`、`csharp_file_paths`）は、
呼び出しからパスを渡す引数を取り出す関数と、語彙、節点の名前の組を `Rules` にまとめて渡す。
ここは、報告した式の範囲の管理、読み書きの呼び出しの後に組み立ての式を拾う順序、
連なりの最も外だけを報告する規則、変数の一段の追跡、囲む関数の修飾名を持つ。
走査が投げる例外（深すぎる入れ子など）は、そのファイルだけを捨てて空を返す。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tree_sitter import Node

from jigane.languages.base import FilePathPattern, FileUsage
from jigane.preconditions.required import required

type Span = tuple[int, int]
# 呼び出しの節点から、パスを渡す引数の節点と使い道の組を返す
type IoArguments = Callable[[Node], Sequence[tuple[Node, FileUsage]]]
# 宣言の節点から（変数の名前、初期値）を返す。変数の宣言でなければ None
type BindingOf = Callable[[Node], tuple[str, Node] | None]


@dataclass(frozen=True)
class Rules:
    """言語ごとの差し込み。節点の名前の組、呼び出しの分解、名前を組む式と文字列を組む式の判定、宣言の読み方。"""

    # 読み書きの呼び出しと、名前を組む式の候補になる節点
    call_nodes: frozenset[str]
    io_arguments: IoArguments
    # `call_nodes` の節点が、名前を組むだけの式（連なりの一段）か
    is_name_building: Callable[[Node], bool]
    # その節点自身が文字列を組み立てるか。部分に持つかは core が子を辿って決める
    builds_string: Callable[[Node], bool]
    # 修飾名に連ねる節点、変数の初期値を探すとき中へ入らない境界、初期値を探す範囲を決める節点
    named_scopes: frozenset[str]
    scope_boundaries: frozenset[str]
    binding_scopes: frozenset[str]
    # 追跡の対象になる、変数を指す式の節点の種類
    identifier_type: str
    binding_of: BindingOf


def text(node: Node) -> str:
    return " ".join(required(node.text).decode().split())


def _span(node: Node) -> Span:
    return (node.start_byte, node.end_byte)


def _contains(outer: Span, inner: Span) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _all_nodes(root: Node) -> list[Node]:
    found: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        found.append(node)
        stack.extend(reversed(node.children))
    return found


def _is_string_building(node: Node, rules: Rules) -> bool:
    """節点自身か、その部分が、文字列を組み立てるか。"""
    return rules.builds_string(node) or any(
        _is_string_building(child, rules) for child in node.children
    )


def qualified_name(node: Node, rules: Rules) -> str:
    names: list[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type in rules.named_scopes:
            name = parent.child_by_field_name("name")
            if name is not None:
                names.append(text(name))
        parent = parent.parent
    return ".".join(reversed(names))


class _Reader:
    def __init__(self, root: Node, path: str) -> None:
        self._root = root
        self._path = path
        self._found: list[tuple[int, FilePathPattern]] = []
        # 報告済みの式の範囲。読み書きの呼び出しの引数を、組み立てとして二重に報告しない
        self._reported: list[Span] = []

    def read(self, rules: Rules) -> list[FilePathPattern]:
        calls = [n for n in _all_nodes(self._root) if n.type in rules.call_nodes]
        for call in calls:
            self._io_call(call, rules)
        for call in calls:
            if rules.is_name_building(call):
                self._name_building(call, rules)
        return [pattern for _, pattern in sorted(self._found, key=lambda item: item[0])]

    def _add(
        self, anchor: Node, expression: Node, usage: FileUsage, covered: Node, rules: Rules
    ) -> None:
        self._reported.append(_span(covered))
        self._found.append(
            (
                anchor.start_byte,
                FilePathPattern(
                    function=qualified_name(anchor, rules),
                    expression=text(expression),
                    usage=usage,
                    path=self._path,
                    line=anchor.start_point[0] + 1,
                ),
            )
        )

    def _add_traced(self, call: Node, argument: Node, usage: FileUsage, rules: Rules) -> None:
        resolved = (
            self._previous_binding(argument, rules)
            if argument.type == rules.identifier_type
            else None
        )
        if resolved is not None:
            # 初期値の側の組み立ては、この行が代わりに表す
            self._reported.append(_span(resolved))
        self._add(call, resolved or argument, usage, argument, rules)

    def _io_call(self, call: Node, rules: Rules) -> None:
        if any(_contains(done, _span(call)) for done in self._reported):
            return
        for argument, usage in rules.io_arguments(call):
            self._add_traced(call, argument, usage, rules)

    def _name_building(self, call: Node, rules: Rules) -> None:
        if any(_contains(done, _span(call)) for done in self._reported):
            return
        if not _is_string_building(call, rules):
            return
        parent = call.parent
        while parent is not None and parent.type not in rules.scope_boundaries:
            if (
                parent.type in rules.call_nodes
                and rules.is_name_building(parent)
                and _is_string_building(parent, rules)
            ):
                # 外側が連なりなら、外側の式だけを報告する
                return
            parent = parent.parent
        self._add(call, call, FileUsage.UNKNOWN, call, rules)

    def _previous_binding(self, variable: Node, rules: Rules) -> Node | None:
        """囲む範囲（無ければファイル）の中で、使う位置より前のその名前の初期値。"""
        name = text(variable)
        best: tuple[int, Node] | None = None
        stack = [self._enclosing_scope(variable, rules)]
        while stack:
            node = stack.pop()
            stack.extend(c for c in node.children if c.type not in rules.scope_boundaries)
            if node.end_byte > variable.start_byte:
                continue
            binding = rules.binding_of(node)
            if binding is None or binding[0] != name:
                continue
            if best is None or node.start_byte > best[0]:
                best = (node.start_byte, binding[1])
        return best[1] if best is not None else None

    def _enclosing_scope(self, node: Node, rules: Rules) -> Node:
        parent = node.parent
        while parent is not None and parent.type not in rules.binding_scopes:
            parent = parent.parent
        return parent or self._root


def file_path_patterns(root: Node, path: str, rules: Rules) -> list[FilePathPattern]:
    """文書順に返す。深すぎて辿れないファイルは空を返す。"""
    try:
        return _Reader(root, path).read(rules)
    except (RecursionError, MemoryError, ValueError):
        # 深く入れ子のファイルは木を辿れない。他のファイルで表は成り立つので、
        # このファイルだけ捨てる
        return []
