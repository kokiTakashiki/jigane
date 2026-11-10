"""Swift の `UserDefaults.standard` の書く側と読む側を、構文木だけから読む道。"""

from tree_sitter import Node

from jigane.languages.base import StoreRole
from jigane.languages.file_paths_core import text
from jigane.languages.swift_file_paths import _Call
from jigane.languages.swift_store_common import Touch, own_nodes
from jigane.languages.swift_store_lexicon import Lexicon

_USER_DEFAULTS = "UserDefaults"
_USER_DEFAULTS_RECEIVER = "UserDefaults.standard"
_WRITING_CALLS = frozenset({"set", "removeObject"})
_READING_CALLS = frozenset(
    {
        "object",
        "string",
        "array",
        "dictionary",
        "data",
        "stringArray",
        "integer",
        "float",
        "double",
        "bool",
        "url",
    }
)


def user_defaults(function: Node, lexicon: Lexicon) -> list[Touch]:
    touches: list[Touch] = []
    for node in own_nodes(function):
        if node.type != "call_expression":
            continue
        call = _Call(node)
        if call.receiver is None or text(call.receiver) != _USER_DEFAULTS_RECEIVER:
            continue
        if call.name in _WRITING_CALLS:
            role = StoreRole.WRITE
        elif call.name in _READING_CALLS:
            role = StoreRole.READ
        else:
            continue  # UserDefaults の鍵を取らないメソッド（register など）は保存物の入口にしない
        argument = call.arguments.get("forKey")
        resolved = lexicon.keys.resolve(argument) if argument is not None else None
        keys = frozenset({resolved[0]}) if resolved else frozenset[str]()
        labels = frozenset({resolved[1]}) if resolved and resolved[1] else frozenset[str]()
        touches.append(Touch(role, _USER_DEFAULTS, keys, labels))
    return touches
