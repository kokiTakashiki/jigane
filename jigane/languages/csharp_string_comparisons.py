"""文字列のリテラルとの比較の抽出（C#）。構文木（tree-sitter）だけから読み、実行も評価もしない。

拾う形は次のとおり。比べる文字列は空でないリテラルに限り、定数でない引数は拾わない。

- 等値と不等: `x == "v"`、`"v" != x`
- 集合への所属: `NAMES.Contains(x)`。`NAMES` は同じファイルの変数で、初期値が配列と集合の初期化子
  （`{...}`、`new[] {...}`、`new HashSet<string> {...}`）かコレクション式（`["a", "b"]`）の文字列の
  リテラルだけのもの。`new[] {...}.Contains(x)` のように直接書く形も拾う。要素ごとに一つ返す
- 接頭辞と接尾辞: `StartsWith("p")`、`EndsWith("s")`
- 正規表現の一致: `Regex.IsMatch(s, "re")`、`Regex.Match(s, "re")`、`Regex.Matches(s, "re")`、
  `new Regex("re")`。パターンの文字列を一つの値とする
- `switch` の `case "v"` は拾わない（Python の `match` の `case` を拾わないのと同じ）
"""

from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import ComparisonKind, StringComparison
from jigane.languages.csharp_value_sets import collection_element, elements
from jigane.languages.value_set_core import (
    literal_strings,
    non_empty,
    ordered_comparisons,
    plain_string,
    walk_nodes,
)

_AFFIX_KINDS = {"StartsWith": ComparisonKind.PREFIX, "EndsWith": ComparisonKind.SUFFIX}
_STATIC_REGEX_FUNCTIONS = frozenset({"IsMatch", "Match", "Matches"})


def _string(node: Node) -> str | None:
    if node.type != "string_literal":
        return None
    return non_empty(plain_string(node, {"string_literal_content"}))


def _literal_strings(node: Node) -> list[str] | None:
    """値の並びを作る式（初期化子、コレクション式）が全部文字列なら、その要素。"""
    if node.type == "initializer_expression":
        return literal_strings([_string(element) for element in elements(node)])
    if node.type == "collection_expression":
        return literal_strings(
            [_string(collection_element(element)) for element in elements(node)]
        )
    if node.type in ("implicit_array_creation_expression", "array_creation_expression"):
        initializer = next((c for c in node.children if c.type == "initializer_expression"), None)
    elif node.type == "object_creation_expression":
        initializer = node.child_by_field_name("initializer")
    else:
        # 並びを作る式以外は、要素を静的に読めない
        return None
    return _literal_strings(initializer) if initializer is not None else None


def _named_sequences(root: Node) -> dict[str, list[str]]:
    """`名前 = 並びのリテラル` と書かれた変数。`名前.Contains(x)` の解決に使う。"""
    found: dict[str, list[str]] = {}
    for node in walk_nodes(root):
        if node.type != "variable_declarator":
            continue
        name = node.child_by_field_name("name")
        if name is None or not any(child.type == "=" for child in node.children):
            # 初期値のない宣言は名前に組を結びつけない
            continue
        if (strings := _literal_strings(node.named_children[-1])) is not None:
            found[(name.text or b"").decode()] = strings
    return found


def _arguments(node: Node) -> list[Node]:
    """呼び出しの引数の式。名前付き引数などの包みの中の式を取り出す。"""
    argument_list = next((c for c in node.children if c.type == "argument_list"), None)
    if argument_list is None:
        return []
    return [
        argument.named_children[-1]
        for argument in argument_list.named_children
        if argument.type == "argument" and argument.named_child_count
    ]


def _invocation(node: Node, named: dict[str, list[str]]) -> Iterator[tuple[str, ComparisonKind]]:
    function = node.child_by_field_name("function")
    arguments = _arguments(node)
    if function is None or function.type != "member_access_expression" or not arguments:
        return
    receiver, name_node = (
        function.child_by_field_name("expression"),
        function.child_by_field_name("name"),
    )
    if receiver is None or name_node is None:
        return
    name = (name_node.text or b"").decode()
    if name == "Contains":
        if receiver.type == "identifier":
            strings = named.get((receiver.text or b"").decode())
        else:
            strings = _literal_strings(receiver)
        for value in strings or []:
            yield value, ComparisonKind.MEMBERSHIP
    elif name in _AFFIX_KINDS:
        if (affix := _string(arguments[0])) is not None:
            yield affix, _AFFIX_KINDS[name]
    elif (
        name in _STATIC_REGEX_FUNCTIONS
        and receiver.type == "identifier"
        and receiver.text == b"Regex"
        and len(arguments) > 1
        and (pattern := _string(arguments[1])) is not None
    ):
        yield pattern, ComparisonKind.REGEX


def _creation(node: Node) -> Iterator[tuple[str, ComparisonKind]]:
    created = node.child_by_field_name("type")
    arguments = _arguments(node)
    if (
        created is not None
        and created.text == b"Regex"
        and arguments
        and (pattern := _string(arguments[0])) is not None
    ):
        yield pattern, ComparisonKind.REGEX


def _binary(node: Node) -> Iterator[tuple[str, ComparisonKind]]:
    operator = node.child_by_field_name("operator")
    if operator is None or operator.text not in (b"==", b"!="):
        # 大小の比較や論理演算は、文字列の値の写しの手がかりにならない
        return
    for side in (node.child_by_field_name("left"), node.child_by_field_name("right")):
        if side is not None and (value := _string(side)) is not None:
            yield value, ComparisonKind.EQUALS


def string_comparisons_of(root: Node, path: str) -> list[StringComparison]:
    """ファイルの文字列との比較を、行の順に返す。"""
    named = _named_sequences(root)
    found: list[tuple[str, ComparisonKind, int]] = []
    for node in walk_nodes(root):
        if node.type == "binary_expression":
            pairs = _binary(node)
        elif node.type == "invocation_expression":
            pairs = _invocation(node, named)
        elif node.type == "object_creation_expression":
            pairs = _creation(node)
        else:
            continue
        found += [(value, kind, node.start_point.row + 1) for value, kind in pairs]
    return ordered_comparisons(found, path)
