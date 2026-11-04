"""文字列のリテラルとの比較の抽出（Java）。構文木（tree-sitter）だけから読み、実行も評価もしない。

拾う形は次のとおり。比べる文字列は空でないリテラルに限り、定数でない引数は拾わない。

- 等値: `x.equals("v")` と `"v".equals(x)`。`==` は参照の比較なので拾わない
- 集合への所属: `NAMES.contains(x)`。`NAMES` は同じファイルの変数で、初期値が配列の初期化子か
  `List.of`、`Set.of`、`Arrays.asList` の文字列のリテラルだけのもの。`Set.of(...).contains(x)` の
  ように直接書く形も拾う。要素ごとに一つ返す
- 接頭辞と接尾辞: `startsWith("p")`、`endsWith("s")`
- 正規表現の一致: `Pattern.compile("re")`、`matches("re")`。パターンの文字列を一つの値とする
- `switch` の `case "v"` は拾わない（Python の `match` の `case` を拾わないのと同じ）
"""

from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import ComparisonKind, StringComparison
from jigane.languages.java_value_sets import elements, factory_arguments
from jigane.languages.value_set_core import (
    literal_strings,
    non_empty,
    ordered_comparisons,
    plain_string,
    walk_nodes,
)

_AFFIX_KINDS = {"startsWith": ComparisonKind.PREFIX, "endsWith": ComparisonKind.SUFFIX}


def _string(node: Node) -> str | None:
    if node.type != "string_literal":
        return None
    return non_empty(plain_string(node, {"string_fragment"}))


def _literal_strings(node: Node) -> list[str] | None:
    """値の並びを作る式（配列の初期化子、`List.of(...)` など）が全部文字列なら、その要素。"""
    if node.type == "array_creation_expression":
        initializer = next((c for c in node.children if c.type == "array_initializer"), None)
        return _literal_strings(initializer) if initializer is not None else None
    if node.type == "array_initializer":
        return literal_strings([_string(element) for element in elements(node)])
    if node.type == "method_invocation":
        arguments = factory_arguments(node)
        if arguments is not None:
            return literal_strings([_string(element) for element in elements(arguments)])
    return None


def _named_sequences(root: Node) -> dict[str, list[str]]:
    """`名前 = 並びのリテラル` と書かれた変数。`名前.contains(x)` の解決に使う。"""
    found: dict[str, list[str]] = {}
    for node in walk_nodes(root):
        if node.type != "variable_declarator":
            continue
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is None or value is None:
            # 初期値のない宣言は名前に組を結びつけない
            continue
        if (strings := _literal_strings(value)) is not None:
            found[(name.text or b"").decode()] = strings
    return found


def _first_argument(node: Node) -> Node | None:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return None
    arguments_list = elements(arguments)
    return arguments_list[0] if arguments_list else None


def _membership(
    receiver: Node, named: dict[str, list[str]]
) -> Iterator[tuple[str, ComparisonKind]]:
    if receiver.type == "identifier":
        strings = named.get((receiver.text or b"").decode())
    else:
        strings = _literal_strings(receiver)
    for value in strings or []:
        yield value, ComparisonKind.MEMBERSHIP


def _call(node: Node, named: dict[str, list[str]]) -> Iterator[tuple[str, ComparisonKind]]:
    name_node, receiver = node.child_by_field_name("name"), node.child_by_field_name("object")
    first = _first_argument(node)
    if name_node is None or receiver is None or first is None:
        return
    name = (name_node.text or b"").decode()
    if name == "equals":
        for side in (receiver, first):
            if (value := _string(side)) is not None:
                yield value, ComparisonKind.EQUALS
    elif name == "contains":
        yield from _membership(receiver, named)
    elif name in _AFFIX_KINDS:
        if (value := _string(first)) is not None:
            yield value, _AFFIX_KINDS[name]
    elif (
        name == "matches"
        or (name == "compile" and receiver.type == "identifier" and receiver.text == b"Pattern")
    ) and (value := _string(first)) is not None:
        yield value, ComparisonKind.REGEX


def string_comparisons_of(root: Node, path: str) -> list[StringComparison]:
    """ファイルの文字列との比較を、行の順に返す。"""
    named = _named_sequences(root)
    found = [
        (value, kind, node.start_point.row + 1)
        for node in walk_nodes(root)
        if node.type == "method_invocation"
        for value, kind in _call(node, named)
    ]
    return ordered_comparisons(found, path)
