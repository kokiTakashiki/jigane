"""文字列のリテラルとの比較の抽出（Swift）。構文木（tree-sitter）だけから読み、実行も評価もしない。

拾う形は次のとおり。比べる文字列は空でないリテラルに限り、定数でない引数は拾わない。

- 等値と不等: `x == "v"`、`"v" != x`
- 集合への所属: `NAMES.contains(x)`。`NAMES` は同じファイルの `let` と `var` で、初期値が文字列の
  リテラルだけの配列と集合のリテラルのもの。`["a", "b"].contains(x)` のように直接書く形も拾う。
  要素ごとに一つ返す
- 接頭辞と接尾辞: `hasPrefix("p")`、`hasSuffix("s")`
- 正規表現の一致: `NSRegularExpression(pattern: "re")`、`Regex("re")`。
  パターンの文字列を一つの値とする。
  `firstMatch(in:range:)` はパターンを引数に取らないので、それ自体は拾わない
- `switch` の `case "v"` は拾わない（Python の `match` の `case` を拾わないのと同じ）
"""

from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import ComparisonKind, StringComparison
from jigane.languages.value_set_core import (
    literal_strings,
    non_empty,
    ordered_comparisons,
    plain_string,
    walk_nodes,
)

_AFFIX_KINDS = {"hasPrefix": ComparisonKind.PREFIX, "hasSuffix": ComparisonKind.SUFFIX}
_REGEX_CONSTRUCTORS = frozenset({"NSRegularExpression", "Regex"})
_COMMENTS = frozenset({"comment", "multiline_comment"})
# 文法が `x == "v" || y` を `x == ("v" || y)` と束ねるので、論理演算の被演算子の端まで降りる
_LOGICAL = frozenset({"disjunction_expression", "conjunction_expression"})


def _string(node: Node) -> str | None:
    if node.type != "line_string_literal":
        return None
    return non_empty(plain_string(node, {"line_str_text"}))


def _literal_strings(node: Node) -> list[str] | None:
    if node.type != "array_literal":
        return None
    elements = [child for child in node.named_children if child.type not in _COMMENTS]
    return literal_strings([_string(element) for element in elements])


def _named_sequences(root: Node) -> dict[str, list[str]]:
    """`let 名前 = 並びのリテラル` と書かれた宣言。`名前.contains(x)` の解決に使う。"""
    found: dict[str, list[str]] = {}
    for node in walk_nodes(root):
        if node.type != "property_declaration":
            continue
        pattern, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        bound = pattern.child_by_field_name("bound_identifier") if pattern is not None else None
        if bound is None or value is None:
            # 初期値のない宣言は名前に組を結びつけない
            continue
        if (strings := _literal_strings(value)) is not None:
            found[(bound.text or b"").decode()] = strings
    return found


def _argument_values(node: Node) -> list[tuple[str | None, Node]]:
    """呼び出しの（ラベル, 値）の並び。"""
    suffix = next((c for c in node.children if c.type == "call_suffix"), None)
    arguments = (
        next((c for c in suffix.children if c.type == "value_arguments"), None) if suffix else None
    )
    if arguments is None:
        return []
    found: list[tuple[str | None, Node]] = []
    for argument in (c for c in arguments.children if c.type == "value_argument"):
        value, label = argument.child_by_field_name("value"), argument.child_by_field_name("name")
        if value is not None:
            found.append(((label.text or b"").decode().rstrip(":") if label else None, value))
    return found


def _called(node: Node) -> tuple[Node | None, str]:
    """呼び出しの受け手（`a.b(...)` の `a`、関数呼び出しなら None）と、呼ばれる名前。"""
    head = node.children[0]
    if head.type == "simple_identifier":
        return None, (head.text or b"").decode()
    if head.type != "navigation_expression":
        return None, ""
    suffix = head.child_by_field_name("suffix")
    name = suffix.child_by_field_name("suffix") if suffix is not None else None
    return head.child_by_field_name("target"), (name.text or b"").decode() if name else ""


def _call(node: Node, named: dict[str, list[str]]) -> Iterator[tuple[str, ComparisonKind]]:
    receiver, name = _called(node)
    arguments = _argument_values(node)
    if not arguments:
        return
    first = arguments[0][1]
    if receiver is None:
        if name in _REGEX_CONSTRUCTORS and (pattern := _string(first)) is not None:
            yield pattern, ComparisonKind.REGEX
    elif name == "contains":
        if receiver.type == "simple_identifier":
            strings = named.get((receiver.text or b"").decode())
        else:
            strings = _literal_strings(receiver)
        for value in strings or []:
            yield value, ComparisonKind.MEMBERSHIP
    elif name in _AFFIX_KINDS and (affix := _string(first)) is not None:
        yield affix, _AFFIX_KINDS[name]


def _edge(node: Node | None, field: str) -> Node | None:
    """論理演算で束ねられた被演算子の、比較に隣り合う端の節点。"""
    while node is not None and node.type in _LOGICAL:
        node = node.child_by_field_name(field)
    return node


def _comparison(node: Node) -> Iterator[tuple[str, ComparisonKind]]:
    operator = node.child_by_field_name("op")
    if operator is None or operator.text not in (b"==", b"!="):
        # 大小の比較や論理演算は、文字列の値の写しの手がかりにならない
        return
    sides = (
        _edge(node.child_by_field_name("lhs"), "rhs"),
        _edge(node.child_by_field_name("rhs"), "lhs"),
    )
    for side in sides:
        if side is not None and (value := _string(side)) is not None:
            yield value, ComparisonKind.EQUALS


def string_comparisons_of(root: Node, path: str) -> list[StringComparison]:
    """ファイルの文字列との比較を、行の順に返す。"""
    named = _named_sequences(root)
    found: list[tuple[str, ComparisonKind, int]] = []
    for node in walk_nodes(root):
        if node.type in ("equality_expression", "infix_expression"):
            pairs = _comparison(node)
        elif node.type == "call_expression":
            pairs = _call(node, named)
        else:
            continue
        found += [(value, kind, node.start_point.row + 1) for value, kind in pairs]
    return ordered_comparisons(found, path)
