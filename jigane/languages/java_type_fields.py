"""型の項目の名前の抽出（Java）。構文木（tree-sitter）だけから読み、実行も評価もしない。

ファイルの直下の `class`、`record`、`enum` が対象。`interface` と注釈の型、入れ子の型は読まない。
項目は宣言の順に、次の名前を並べる。

- インスタンスのフィールド（`int a, b;` は名前ごと。アクセス修飾子では除かない）
- `record` の成分
- `enum` の定数と、`;` の後のインスタンスのフィールド

`static` のフィールドは除く。
"""

from collections.abc import Callable, Iterator

from tree_sitter import Node

from jigane.languages.base import TypeFields
from jigane.languages.java_nodes import TYPE_KINDS
from jigane.languages.node_text import text_or_empty

_READ_KINDS = frozenset({"class", "record", "enum"})


def _is_instance(node: Node) -> bool:
    modifiers = next((child for child in node.children if child.type == "modifiers"), None)
    return modifiers is None or not any(child.type == "static" for child in modifiers.children)


def _field_declaration_names(node: Node) -> list[str]:
    names: list[str] = []
    for declarator in node.children:
        name = declarator.child_by_field_name("name")
        if declarator.type == "variable_declarator" and name is not None:
            names.append(text_or_empty(name))
    return names


def _member_names(member: Node) -> list[str]:
    if member.type == "field_declaration" and _is_instance(member):
        return _field_declaration_names(member)
    if member.type == "enum_constant":
        name = member.child_by_field_name("name")
        return [text_or_empty(name)] if name is not None else []
    # static のフィールド、メソッド、コンストラクタ、入れ子の型
    return []


def _record_components(node: Node) -> list[str]:
    if TYPE_KINDS[node.type] != "record":
        # 成分を項目に数えるのは record だけ
        return []
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return []
    names = [parameter.child_by_field_name("name") for parameter in parameters.children]
    return [text_or_empty(name) for name in names if name is not None]


def _members(body: Node) -> Iterator[Node]:
    for member in body.children:
        if member.type == "enum_body_declarations":
            # enum の `;` の後の宣言は、この節点の子に並ぶ
            yield from member.children
        else:
            yield member


def _field_names(node: Node) -> tuple[str, ...]:
    names = _record_components(node)
    body = node.child_by_field_name("body")
    if body is not None:
        for member in _members(body):
            names += _member_names(member)
    return tuple(dict.fromkeys(names))


def _read(root: Node, path: str) -> list[TypeFields]:
    found: list[TypeFields] = []
    for node in root.children:
        if TYPE_KINDS.get(node.type) not in _READ_KINDS:
            continue
        name = node.child_by_field_name("name")
        fields = _field_names(node)
        if name is not None and fields:
            found.append(TypeFields(text_or_empty(name), path, node.start_point.row + 1, fields))
    return found


def type_fields_of(parse_root: Callable[[], Node], path: str) -> list[TypeFields]:
    """ファイルのトップレベルの型を、文書順に返す。構文木を作れないファイルは空を返す。"""
    try:
        return _read(parse_root(), path)
    except (RecursionError, MemoryError, ValueError):
        # 深く入れ子のファイルは木を作れも辿れもしない。
        # 他のファイルで表は成り立つので、このファイルだけ捨てる
        return []
