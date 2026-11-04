"""値の組の抽出（C#）。構文木（tree-sitter）だけから読み、実行も評価もしない。

拾う形は次のとおり。要素がすべて短い文字列のリテラルで、三つ以上のものだけを返す。

- 配列と集合の初期化子（`{...}`、`new[] {...}`、`new string[] {...}`、
  `new HashSet<string> {...}`、`new List<string> {...}`）
- コレクション式（`["a", "b", "c"]`）
- 型の本体が、短い文字列の `const` の宣言だけで成るもの（文字列の値を持つ定数の並び）

区別（`ValueSet.kind`）は、
宣言の名前を取り出せた並びを名前に束ねた定義（NAMED）、
名前が取れず、
並びが変数宣言子にも代入式にも含まれない並びを名前の無いリテラル（LITERAL）、
名前は取れないが変数宣言子か代入式の中にある並び
（`new List<string>(new[] {...})` の包みなど）を未決（UNDETERMINED）とする。
変数宣言の初期値と、定数を並べた型の名前が名前。
メソッドの引数に直に書いた並びは LITERAL。
名前を取れなかったことは、リテラルである証拠にならない。
名前の綴りは見ない。
"""

from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import ValueSet
from jigane.languages.value_set_core import (
    declared_name,
    field_text,
    inside_node_types,
    ordered_value_sets,
    plain_string,
    value_set_of,
    walk_nodes,
)

_TYPE_DECLARATIONS = frozenset(
    {"class_declaration", "struct_declaration", "record_declaration", "interface_declaration"}
)
_COMMENTS = frozenset({"comment"})
_DEFINITION_NODES = frozenset({"variable_declarator", "assignment_expression"})
_INITIALIZER_WRAPPERS = frozenset(
    {
        "array_creation_expression",
        "implicit_array_creation_expression",
        "object_creation_expression",
    }
)


def _string(node: Node) -> str | None:
    if node.type != "string_literal":
        return None
    return plain_string(node, {"string_literal_content"})


def elements(node: Node) -> list[Node]:
    return [child for child in node.named_children if child.type not in _COMMENTS]


def collection_element(node: Node) -> Node:
    """コレクション式の要素の包みを外す。展開（`..x`）は式の位置に来ないので外れる。"""
    if node.type == "collection_element" and node.named_child_count == 1:
        inner = node.named_children[0]
        return inner.named_children[0] if inner.type == "expression_element" else inner
    return node


def _constant_values(declarations: Node) -> frozenset[str] | None:
    values: list[str | None] = []
    for member in elements(declarations):
        if member.type != "field_declaration" or not any(
            child.type == "modifier" and child.text == b"const" for child in member.children
        ):
            return None
        declaration = next((c for c in member.children if c.type == "variable_declaration"), None)
        if declaration is None:
            return None
        for declarator in (c for c in declaration.children if c.type == "variable_declarator"):
            has_value = any(child.type == "=" for child in declarator.children)
            values.append(_string(declarator.named_children[-1]) if has_value else None)
    return value_set_of(values)


def _found(root: Node) -> Iterator[tuple[frozenset[str], int, str | None, bool]]:
    for node in walk_nodes(root):
        values: frozenset[str] | None = None
        name = declared_name(node, _INITIALIZER_WRAPPERS, "variable_declarator", "name")
        if node.type == "initializer_expression":
            values = value_set_of([_string(element) for element in elements(node)])
        elif node.type == "collection_expression":
            values = value_set_of(
                [_string(collection_element(element)) for element in elements(node)]
            )
        elif node.type in _TYPE_DECLARATIONS:
            declarations = node.child_by_field_name("body")
            if declarations is not None:
                values = _constant_values(declarations)
            name = field_text(node, "name")
        if values:
            yield (
                values,
                node.start_point.row + 1,
                name,
                inside_node_types(node, _DEFINITION_NODES),
            )


def value_sets_of(root: Node, path: str) -> list[ValueSet]:
    """ファイルの値の組を、定義の行の順に返す。"""
    return ordered_value_sets(_found(root), path)
