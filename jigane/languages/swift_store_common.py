"""Swift の保存物の道が共有する、触れ方の型と構文木の小さな道具。"""

from collections.abc import Iterator
from dataclasses import dataclass

from tree_sitter import Node

from jigane.languages.base import StoreFormat, StoreRole
from jigane.languages.file_paths_core import text
from jigane.languages.swift_nodes import DECLARED_FUNCTION_NODES

TYPE_NODES = frozenset({"class_declaration"})
ACCESSOR_NODES = {
    "computed_getter": "get",
    "computed_setter": "set",
    "willset_clause": "willSet",
    "didset_clause": "didSet",
}
FUNCTION_NODES = DECLARED_FUNCTION_NODES | frozenset(ACCESSOR_NODES)


@dataclass(frozen=True)
class Touch:
    """道が一つの呼び出しから読んだ触れ方。鍵が解けなければ keys は空。"""

    role: StoreRole
    name: str
    keys: frozenset[str]
    labels: frozenset[str]
    kind: StoreFormat = StoreFormat.CUSTOM
    types: frozenset[str] = frozenset()


def ancestors(node: Node) -> Iterator[Node]:
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def suffix(navigation: Node) -> str | None:
    member = navigation.child_by_field_name("suffix")
    name = member.child_by_field_name("suffix") if member is not None else None
    return text(name) if name is not None else None


def type_name(node: Node) -> str | None:
    name = node.child_by_field_name("name")
    # 拡張は `extension A.B` の最後の部品を、拡張した型の名前とする
    return text(name).rsplit(".", 1)[-1] if name is not None else None


def own_nodes(function: Node) -> Iterator[Node]:
    """関数の本体の節点。入れ子の関数は、それ自身の単位として読むので入らない。"""
    stack = list(reversed(function.children))
    while stack:
        node = stack.pop()
        yield node
        if node.type not in FUNCTION_NODES:
            stack.extend(reversed(node.children))


def positional_first(call: Node) -> Node | None:
    """ラベルの無い最初の引数の式。"""
    call_suffix = next((c for c in call.children if c.type == "call_suffix"), None)
    arguments = [
        c
        for v in (call_suffix.children if call_suffix else [])
        if v.type == "value_arguments"
        for c in v.children
    ]
    first = next((c for c in arguments if c.type == "value_argument"), None)
    if first is None or first.child_by_field_name("name") is not None:
        return None
    return first.child_by_field_name("value")
