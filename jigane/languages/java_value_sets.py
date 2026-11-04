"""値の組の抽出（Java）。構文木（tree-sitter）だけから読み、実行も評価もしない。

拾う形は次のとおり。要素がすべて短い文字列のリテラルで、三つ以上のものだけを返す。

- 配列の初期化子（`new String[]{...}`、`String[] a = {...}`）
- `List.of(...)`、`Set.of(...)`、`Arrays.asList(...)` の引数
- 列挙型で、すべての定数が短い文字列を一つだけ引数に持つもの（`X("x")`）の引数の集合

区別（`ValueSet.kind`）は、
宣言の名前を取り出せた並びを名前に束ねた定義（NAMED）、
名前が取れず、
並びが変数宣言子にも代入式にも含まれない並びを名前の無いリテラル（LITERAL）、
名前は取れないが変数宣言子か代入式の中にある並び
（`new HashSet<>(Arrays.asList(...))` の包みなど）を未決（UNDETERMINED）とする。
変数宣言の初期値と列挙型の名前が名前。
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

_COLLECTION_FACTORIES = {"List": "of", "Set": "of", "Arrays": "asList"}
_COMMENTS = frozenset({"line_comment", "block_comment"})
_INITIALIZER_WRAPPERS = frozenset({"array_creation_expression"})
_DEFINITION_NODES = frozenset({"variable_declarator", "assignment_expression"})


def _string(node: Node) -> str | None:
    return plain_string(node, {"string_fragment"}) if node.type == "string_literal" else None


def elements(node: Node) -> list[Node]:
    return [child for child in node.named_children if child.type not in _COMMENTS]


def factory_arguments(node: Node) -> Node | None:
    """`List.of(...)` のような、値の並びを作る呼び出しの引数の並び。"""
    receiver = node.child_by_field_name("object")
    name = node.child_by_field_name("name")
    if receiver is None or name is None or receiver.type != "identifier":
        return None
    if _COLLECTION_FACTORIES.get((receiver.text or b"").decode()) != (name.text or b"").decode():
        return None
    return node.child_by_field_name("arguments")


def _enum_values(node: Node) -> frozenset[str] | None:
    body = node.child_by_field_name("body")
    if body is None:
        return None
    values: list[str | None] = []
    for constant in (child for child in body.named_children if child.type == "enum_constant"):
        arguments = constant.child_by_field_name("arguments")
        items = elements(arguments) if arguments is not None else []
        values.append(_string(items[0]) if len(items) == 1 else None)
    return value_set_of(values)


def _found(root: Node) -> Iterator[tuple[frozenset[str], int, str | None, bool]]:
    for node in walk_nodes(root):
        values: frozenset[str] | None = None
        name = declared_name(node, _INITIALIZER_WRAPPERS, "variable_declarator", "name")
        if node.type == "array_initializer":
            values = value_set_of([_string(element) for element in elements(node)])
        elif node.type == "method_invocation":
            arguments = factory_arguments(node)
            if arguments is not None:
                values = value_set_of([_string(element) for element in elements(arguments)])
        elif node.type == "enum_declaration":
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
