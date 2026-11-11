"""Swift の Keychain（`SecItemAdd` などの Security の関数）の書く側と読む側の道。

読みは三段に分ける。呼び出しを見つける段、問い合わせの辞書を辿る段、
鍵を解いて触れ方を組み立てる段である。
"""

from collections.abc import Iterator
from typing import NamedTuple

from tree_sitter import Node

from jigane.languages.base import StoreRole
from jigane.languages.file_paths_core import text
from jigane.languages.swift_file_paths import Call
from jigane.languages.swift_store_common import Touch, own_nodes, positional_first
from jigane.languages.swift_store_lexicon import Lexicon, Vocabulary

_KEYCHAIN = "Keychain"
_WRITING_CALLS = frozenset({"SecItemAdd", "SecItemUpdate", "SecItemDelete"})
_READING_CALLS = frozenset({"SecItemCopyMatching"})
_ACCOUNT_KEY = "kSecAttrAccount"


class _KeychainCall(NamedTuple):
    """見つけた Security の関数の呼び出しの役割と、ラベルの無い最初の引数の式。"""

    role: StoreRole
    argument: Node | None


def _keychain_calls(function: Node) -> Iterator[_KeychainCall]:
    for node in own_nodes(function):
        if node.type != "call_expression":
            continue
        call = Call(node)
        if call.receiver is not None:
            continue  # 受け手の有る同名のメソッドは、Security の関数でない
        if call.name in _WRITING_CALLS:
            yield _KeychainCall(StoreRole.WRITE, positional_first(node))
        elif call.name in _READING_CALLS:
            yield _KeychainCall(StoreRole.READ, positional_first(node))


def _strip_cast(node: Node) -> Node:
    """`x as T` の `as` を剥がして x を返す。`as` でなければそのまま。"""
    while node.type == "as_expression":
        inner = node.child_by_field_name("expr")
        if inner is None:
            break
        node = inner
    return node


def _query_dictionary(function: Node, argument: Node) -> Node | None:
    """問い合わせの辞書のリテラル。第一引数に直に書くか、同じ関数の宣言を一段辿る。"""
    argument = _strip_cast(argument)
    if argument.type == "dictionary_literal":
        return argument
    if argument.type != "simple_identifier":
        return None
    found: list[Node | None] = []
    for node in own_nodes(function):
        if node.type != "property_declaration":
            continue
        pattern = node.child_by_field_name("name")
        identifier = pattern.child_by_field_name("bound_identifier") if pattern else None
        if identifier is not None and text(identifier) == text(argument):
            value = node.child_by_field_name("value")
            is_dictionary = value is not None and value.type == "dictionary_literal"
            found.append(value if is_dictionary else None)
    # 同じ名前を二度宣言する関数は、どちらの辞書か決められないので辿らない
    return found[0] if len(found) == 1 else None


def _account_keys(dictionary: Node, vocabulary: Vocabulary) -> frozenset[str]:
    """辞書の `kSecAttrAccount` の値を語彙で解いた文字列。解けなければ空。"""
    entry_key: Node | None = None
    for index, child in enumerate(dictionary.children):
        field = dictionary.field_name_for_child(index)
        if field == "key":
            entry_key = _strip_cast(child)
        elif field == "value" and entry_key is not None:
            if text(entry_key) == _ACCOUNT_KEY:
                resolved = vocabulary.resolve(_strip_cast(child))
                return frozenset({resolved[0]}) if resolved else frozenset()
            entry_key = None
    return frozenset()


def keychain(function: Node, lexicon: Lexicon) -> list[Touch]:
    touches: list[Touch] = []
    for call in _keychain_calls(function):
        dictionary = (
            _query_dictionary(function, call.argument) if call.argument is not None else None
        )
        keys = _account_keys(dictionary, lexicon.keys) if dictionary is not None else frozenset()
        touches.append(Touch(call.role, _KEYCHAIN, keys, frozenset()))
    return touches
