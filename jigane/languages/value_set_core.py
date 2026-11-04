"""値の組の抽出で、言語に依らない部品。しきい値と、構文木の走査と、要素の集合への束ねを持つ。"""

from collections.abc import Iterable, Iterator, Sequence

from tree_sitter import Node

from jigane.languages.base import (
    ComparisonKind,
    DefinitionKind,
    StringComparison,
    ValueSet,
)

# 短い語だけを値とみなす。長い文字列は文章であり、閉じた値の集合の要素ではない
MAX_VALUE_LENGTH = 40
# 三つに満たない並びは、二値の選択や対であって、値の組とは言えない
MIN_VALUES = 3


def short_value(text: str | None) -> str | None:
    """空でなく上限以下の文字列だけを値とし、外れれば None。"""
    if text is None or not 0 < len(text) <= MAX_VALUE_LENGTH:
        return None
    return text


def value_set_of(values: Sequence[str | None]) -> frozenset[str] | None:
    """要素がすべて短い文字列で、数が下限以上なら、その集合。一つでも外れれば None。"""
    checked = [short_value(value) for value in values]
    if len(checked) < MIN_VALUES or any(value is None for value in checked):
        return None
    return frozenset(value for value in checked if value is not None)


def walk_nodes(root: Node) -> Iterator[Node]:
    """構文木の全節点を文書順に返す。"""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def plain_string(node: Node, content_types: Iterable[str]) -> str | None:
    """文字列リテラルの本文。エスケープや補間を含む、または文字列でない節点は None。

    content_types は、本文の断片として許す子の型。引用符の子は区切りなので読み飛ばす。
    """
    allowed = frozenset(content_types)
    parts: list[str] = []
    for child in node.children:
        if child.type in allowed:
            parts.append((child.text or b"").decode())
        elif child.type != '"':
            # エスケープ、補間、複数行の断片は、静的に値が決まらない
            return None
    return "".join(parts)


def ordered_value_sets(
    found: Iterable[tuple[frozenset[str], int, str | None, bool]], path: str
) -> list[ValueSet]:
    """見つけた（値の集合, 行, 名前, 宣言か代入の中か）を、定義の行の順の `ValueSet` にする。"""
    return [
        ValueSet(values, path, line, name, definition_kind(name, in_definition=in_definition))
        for values, line, name, in_definition in sorted(found, key=lambda f: f[1])
    ]


def definition_kind(name: str | None, *, in_definition: bool) -> DefinitionKind:
    """名前が取れれば NAMED。取れなくても、並びが宣言か代入の中にあれば UNDETERMINED。

    名前を取れなかったことは、リテラルである証拠にならない。LITERAL は、並びが宣言にも
    代入にも含まれないと構文木から確かめられたときだけ。名前の綴りは見ない。
    """
    if name is not None:
        return DefinitionKind.NAMED
    return DefinitionKind.UNDETERMINED if in_definition else DefinitionKind.LITERAL


def inside_node_types(node: Node, types: Iterable[str]) -> bool:
    """節点が、指定した型の節点の中（祖先）にあるか。"""
    wanted = frozenset(types)
    parent = node.parent
    while parent is not None:
        if parent.type in wanted:
            return True
        parent = parent.parent
    return False


def declared_name(
    node: Node, wrappers: Iterable[str], declarator_type: str, name_field: str
) -> str | None:
    """値の節点が、宣言の初期値として書かれているとき、その宣言の名前。

    wrappers は、初期値と宣言の間に挟まる節点の型（`new String[]{...}` の包みなど）。
    宣言に行き着かなければ None。
    """
    wrapping = frozenset(wrappers)
    parent = node.parent
    while parent is not None and parent.type in wrapping:
        parent = parent.parent
    if parent is None or parent.type != declarator_type:
        return None
    name = parent.child_by_field_name(name_field)
    return (name.text or b"").decode() if name is not None else None


def field_text(node: Node, field: str) -> str | None:
    """節点のフィールドの本文。フィールドが無ければ None。"""
    child = node.child_by_field_name(field)
    return (child.text or b"").decode() if child is not None else None


def non_empty(text: str | None) -> str | None:
    """空でない文字列だけを比較の値とし、外れれば None。

    空文字列は何にも一致する接頭辞になるので除く。長さの上限は設けない。
    """
    return text or None


def literal_strings(values: Sequence[str | None]) -> list[str] | None:
    """並びが空でなく、要素がすべて文字列のリテラルなら、その要素。一つでも外れれば None。"""
    if not values or any(value is None for value in values):
        return None
    return [value for value in values if value is not None]


def ordered_comparisons(
    found: Iterable[tuple[str, ComparisonKind, int]], path: str
) -> list[StringComparison]:
    """見つけた（値, 種類, 行）を、行の順の `StringComparison` にする。"""
    return [
        StringComparison(value, kind, path, line)
        for value, kind, line in sorted(found, key=lambda f: f[2])
    ]
