"""正規表現の引数が局所の識別子のとき、その識別子に一度だけ代入された値の式を探す。四つの言語で共通。"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from tree_sitter import Node

from jigane.preconditions.required import required


@dataclass(frozen=True)
class LocalDeclaration:
    """局所の宣言。`value` は初期値の式、`statement` はそれを含む宣言の文。"""

    value: Node
    statement: Node


@dataclass(frozen=True)
class LocalRules:
    """言語ごとに違う節点の型と判定。共通の手順だけを `local_value` が持つ。"""

    scopes: frozenset[str]
    identifier: str
    # 識別子が宣言の名前なら、その宣言を返す。宣言でなければ None
    declaration: Callable[[Node], LocalDeclaration | None]
    # 関数の本体の直下の文が並ぶ節点
    top_level: Callable[[Node], Node | None]
    # 識別子が値の読み取りの位置にあるか。書き込みか束縛か判別できないものは False
    is_read: Callable[[Node], bool]


def _walk(root: Node) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _enclosing_scope(use: Node, rules: LocalRules) -> Node | None:
    scope = use.parent
    while scope is not None and scope.type not in rules.scopes:
        scope = scope.parent
    return scope


def _sole_declaration(scope: Node, use: Node, rules: LocalRules) -> LocalDeclaration | None:
    """`scope` の中で `use` と同名の識別子が宣言一つと読み取りだけなら、その宣言。"""
    name = required(use.text)
    found: LocalDeclaration | None = None
    for node in _walk(scope):
        if node.type != rules.identifier or node.text != name or node == use:
            continue
        declared = rules.declaration(node)
        if declared is not None:
            if found is not None:
                return None
            found = declared
        elif not rules.is_read(node):
            # 再代入、引数、ループの変数、複合代入、参照渡しなど。値が変わりうるので解かない
            return None
    return found


def _is_settled_at(found: LocalDeclaration, use: Node, scope: Node, rules: LocalRules) -> bool:
    # 分岐やループの中だけの宣言は、使う位置で値が決まっているとは言えない
    if found.statement.parent is None or found.statement.parent != rules.top_level(scope):
        return False
    return found.statement.end_byte <= use.start_byte


def local_value(use: Node, rules: LocalRules) -> Node | None:
    """`use` の識別子が、関数の中で一度だけ宣言され、他に書き込みの無いなら、その初期値の式。"""
    if use.type != rules.identifier:
        return None
    scope = _enclosing_scope(use, rules)
    if scope is None:
        return None
    found = _sole_declaration(scope, use, rules)
    if found is None or not _is_settled_at(found, use, scope, rules):
        return None
    return found.value
