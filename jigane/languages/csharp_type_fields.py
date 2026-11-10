"""型の項目の名前の抽出（C#）。構文木（tree-sitter）だけから読み、実行も評価もしない。

トップレベルの `class`、`struct`、`record`（`record struct` を含む）、`enum` が対象。
ファイルの直下、ブロックの名前空間、`#if` の中の型を読み、入れ子の型は読まない。
項目は宣言の順に、次の名前を並べる。

- インスタンスのフィールド（`int a, b;` は名前ごと。アクセス修飾子では除かない）
- アクセサに本体の無い自動実装プロパティ
- `record` の位置引数
- `enum` の列挙子

`static` と `const` のフィールド、`static` のプロパティ、本体か式本体を持つプロパティ、
`event` は除く。`partial` の型は宣言ごとに別の型として返す。
"""

from collections.abc import Callable, Iterator

from tree_sitter import Node

from jigane.languages.base import TypeFields
from jigane.languages.csharp_nodes import NAMESPACES, TYPE_KINDS, flatten, namespace_container
from jigane.languages.node_text import text_or_empty

_READ_KINDS = frozenset({"class", "struct", "record", "enum"})
_BODIES = frozenset({"declaration_list", "enum_member_declaration_list"})
_TYPE_LEVEL_MODIFIERS = frozenset({b"static", b"const"})
_BODY_NODES = frozenset({"block", "arrow_expression_clause"})


def _is_instance(node: Node) -> bool:
    return not any(
        child.type == "modifier" and child.text in _TYPE_LEVEL_MODIFIERS for child in node.children
    )


def _is_auto_property(node: Node) -> bool:
    accessors = next((child for child in node.children if child.type == "accessor_list"), None)
    if accessors is None:
        # 式本体（`=>`）の宣言はアクセサの並びを持たない
        return False
    return not any(
        part.type in _BODY_NODES for accessor in accessors.children for part in accessor.children
    )


def _field_declaration_names(node: Node) -> list[str]:
    names: list[str] = []
    for declaration in node.children:
        if declaration.type != "variable_declaration":
            continue
        for declarator in declaration.children:
            name = declarator.child_by_field_name("name")
            if declarator.type == "variable_declarator" and name is not None:
                names.append(text_or_empty(name))
    return names


def _member_names(member: Node) -> list[str]:
    name = member.child_by_field_name("name")
    if member.type == "field_declaration" and _is_instance(member):
        return _field_declaration_names(member)
    if member.type == "property_declaration" and _is_instance(member):
        return [text_or_empty(name)] if name is not None and _is_auto_property(member) else []
    if member.type == "enum_member_declaration" and name is not None:
        return [text_or_empty(name)]
    # event、メソッドなど。event_field_declaration もここへ来て項目に数えない
    return []


def _positional_parameters(node: Node) -> list[str]:
    if TYPE_KINDS[node.type] != "record":
        # 位置引数を項目に数えるのは record だけ。class と struct の主コンストラクタは別
        return []
    parameters = next((child for child in node.children if child.type == "parameter_list"), None)
    if parameters is None:
        return []
    names = [parameter.child_by_field_name("name") for parameter in parameters.children]
    return [text_or_empty(name) for name in names if name is not None]


def _field_names(node: Node) -> tuple[str, ...]:
    names = _positional_parameters(node)
    body = next((child for child in node.children if child.type in _BODIES), None)
    if body is not None:
        for member in flatten(body):
            names += _member_names(member)
    return tuple(dict.fromkeys(names))


def _top_level_types(container: Node) -> Iterator[Node]:
    for node in flatten(container):
        if node.type in NAMESPACES:
            yield from _top_level_types(namespace_container(node))
        elif TYPE_KINDS.get(node.type) in _READ_KINDS:
            yield node


def _read(root: Node, path: str) -> list[TypeFields]:
    found: list[TypeFields] = []
    for node in _top_level_types(root):
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
