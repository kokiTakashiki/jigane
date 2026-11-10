"""型の項目の名前の抽出（Swift）。構文木（tree-sitter）だけから読み、実行も評価もしない。

トップレベルの `struct`、`class`、`actor`、`enum` が対象。
項目は、宣言の順の格納プロパティ（`let`、`var`）の名前と、`enum` の `case` の名前。
計算プロパティと、型に属する `static`、`class` のプロパティは除く。
`extension` と入れ子の型は読まない。タプルの分解宣言（`let (x, y) = ...`）は名前を拾わない。
"""

from tree_sitter import Node

from jigane.languages.base import TypeFields
from jigane.languages.swift_nodes import TYPE_KEYWORDS

_BODIES = frozenset({"class_body", "enum_class_body"})
_TYPE_LEVEL_MODIFIERS = frozenset({b"static", b"class"})


def _is_stored_instance_property(node: Node) -> bool:
    if any(child.type == "computed_property" for child in node.children):
        return False
    modifiers = next((child for child in node.children if child.type == "modifiers"), None)
    if modifiers is None:
        return True
    return not any(
        modifier.type == "property_modifier" and modifier.text in _TYPE_LEVEL_MODIFIERS
        for modifier in modifiers.children
    )


def _property_name(node: Node) -> str | None:
    pattern = next((child for child in node.children if child.type == "pattern"), None)
    if pattern is None or any(child.type == "pattern" for child in pattern.children):
        # 分解宣言の pattern は子に pattern を持つ
        return None
    name = next((child for child in pattern.children if child.type == "simple_identifier"), None)
    return (name.text or b"").decode() if name is not None else None


def _field_names(body: Node) -> tuple[str, ...]:
    names: list[str] = []
    for member in body.children:
        if member.type == "property_declaration" and _is_stored_instance_property(member):
            name = _property_name(member)
            if name is not None:
                names.append(name)
        elif member.type == "enum_entry":
            names += [
                (child.text or b"").decode()
                for child in member.children
                if child.type == "simple_identifier"
            ]
    return tuple(dict.fromkeys(names))


def type_fields_of(root: Node, path: str) -> list[TypeFields]:
    """ファイルのトップレベルの型を、文書順に返す。"""
    found: list[TypeFields] = []
    for node in root.children:
        if node.type != "class_declaration":
            continue
        if not any(child.type in TYPE_KEYWORDS for child in node.children):
            # extension も class_declaration で表され、キーワードで型の宣言と見分ける
            continue
        name = node.child_by_field_name("name")
        body = next((child for child in node.children if child.type in _BODIES), None)
        if name is None or body is None:
            continue
        fields = _field_names(body)
        if fields:
            line = node.start_point.row + 1
            found.append(TypeFields((name.text or b"").decode(), path, line, fields))
    return found
