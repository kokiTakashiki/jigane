"""値の組の抽出（Swift）。構文木（tree-sitter）だけから読み、実行も評価もしない。

拾う形は次のとおり。要素がすべて短い文字列のリテラルで、三つ以上のものだけを返す。

- 配列と集合のリテラル（`["a", "b", "c"]`。集合も同じ記法で書く）
- 生の値が `String` の列挙型で、すべての case が文字列の値を持つもの（`case a = "v"`）

区別（`ValueSet.kind`）は、
宣言の名前を取り出せた並びを名前に束ねた定義（NAMED）、
名前が取れず、
並びが `let`/`var` の宣言にも代入にも含まれない並びを名前の無いリテラル（LITERAL）、
名前は取れないが宣言か代入の中にある並び
（`Set(["a", "b", "c"])` の包みなど）を未決（UNDETERMINED）とする。
`let`/`var` の束縛と列挙型の名前が名前。
関数の引数に直に書いた並びは LITERAL。
名前を取れなかったことは、リテラルである証拠にならない。
名前の綴りは見ない。
"""

from collections.abc import Iterator

from tree_sitter import Node

from jigane.languages.base import ValueSet
from jigane.languages.swift_nodes import string_literal
from jigane.languages.value_set_core import (
    field_text,
    inside_node_types,
    ordered_value_sets,
    value_set_of,
    walk_nodes,
)

_COMMENTS = frozenset({"comment", "multiline_comment"})
_DEFINITION_NODES = frozenset({"property_declaration", "assignment"})


def _is_string_enum(node: Node) -> bool:
    if not any(child.type == "enum" for child in node.children):
        return False
    return any(
        child.type == "inheritance_specifier" and child.text == b"String"
        for child in node.children
    )


def _enum_values(node: Node) -> frozenset[str] | None:
    body = next((child for child in node.children if child.type == "enum_class_body"), None)
    if body is None:
        return None
    values: list[str | None] = []
    for entry in (child for child in body.children if child.type == "enum_entry"):
        for index, child in enumerate(entry.children):
            if child.type != "simple_identifier":
                continue
            following = entry.children[index + 1 : index + 3]
            # 値を書かない case の生の値は名前そのものだが、静的には書かれていないので外す
            has_value = len(following) > 1 and following[0].type == "="
            values.append(string_literal(following[1]) if has_value else None)
    return value_set_of(values)


def _declared_name(node: Node) -> str | None:
    """`let`/`var` の初期値として書かれた並びの、束縛される名前。"""
    declaration = node.parent
    if declaration is None or declaration.type != "property_declaration":
        return None
    pattern = declaration.child_by_field_name("name")
    return field_text(pattern, "bound_identifier") if pattern is not None else None


def _found(root: Node) -> Iterator[tuple[frozenset[str], int, str | None, bool]]:
    for node in walk_nodes(root):
        values: frozenset[str] | None = None
        name: str | None = None
        if node.type == "array_literal":
            name = _declared_name(node)
            elements = [child for child in node.named_children if child.type not in _COMMENTS]
            values = value_set_of([string_literal(element) for element in elements])
        elif node.type == "class_declaration" and _is_string_enum(node):
            values = _enum_values(node)
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
