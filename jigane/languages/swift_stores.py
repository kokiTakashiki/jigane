"""Swift の保存物（UserDefaults、Codable の JSON、Keychain）の書く側と読む側を構文木から読む。

実行も import もしない。

鍵の語彙と Codable の型の項目は渡された全ファイルから先に集め、関数ごとに道を読む。
道は関数の節点から触れ方の並びを返す関数で、保存物の種類ごとのモジュールに置き、
`_ROADS` に足すと保存物の種類が増える。ここはファイルの読み込み、関数の列挙、
道の振り分け、`StoreAccess` の組み立てだけを持つ。
"""

from collections.abc import Callable, Iterator, Mapping

from tree_sitter import Node

from jigane.languages.base import StoreAccess, StoreRole, StoreSide
from jigane.languages.file_paths_core import qualified_name, text
from jigane.languages.swift_file_paths import RULES as SWIFT_RULES
from jigane.languages.swift_nodes import (
    DECLARED_FUNCTION_NODES,
    function_name_of,
)
from jigane.languages.swift_store_codable import codable_json
from jigane.languages.swift_store_common import ACCESSOR_NODES, Touch, ancestors
from jigane.languages.swift_store_keychain import keychain
from jigane.languages.swift_store_lexicon import Lexicon, Road
from jigane.languages.swift_store_userdefaults import user_defaults
from jigane.languages.value_set_core import walk_nodes

# 道を足すとここに並べる
_ROADS: tuple[Road, ...] = (user_defaults, codable_json, keychain)


def _functions(root: Node) -> Iterator[tuple[Node, str]]:
    for node in walk_nodes(root):
        if node.type in DECLARED_FUNCTION_NODES:
            own = function_name_of(node)
            if own is not None:
                scope = qualified_name(node, SWIFT_RULES)
                yield node, ".".join(filter(None, (scope, text(own.node))))
        elif node.type in ACCESSOR_NODES:
            yield from _accessor(node)


def _accessor(node: Node) -> Iterator[tuple[Node, str]]:
    """計算プロパティのアクセサを `型.プロパティ.get` の形の単位にする。"""
    declaration = next((a for a in ancestors(node) if a.type == "property_declaration"), None)
    own = declaration.child_by_field_name("name") if declaration is not None else None
    if own is not None:
        scope = qualified_name(node, SWIFT_RULES)
        yield node, ".".join(filter(None, (scope, text(own), ACCESSOR_NODES[node.type])))


def _side(role: StoreRole, function: str, path: str, touches: list[Touch]) -> StoreSide | None:
    own = [t for t in touches if t.role is role]
    if not own:
        return None
    return StoreSide(
        role=role,
        function=function,
        path=path,
        kind=own[0].kind,
        keys=frozenset().union(*(t.keys for t in own)),
        types=frozenset().union(*(t.types for t in own)),
        labels=frozenset().union(*(t.labels for t in own)),
    )


def store_accesses(sources: Mapping[str, str], parse: Callable[[str], Node]) -> list[StoreAccess]:
    """関数ごとの保存物の書く側と読む側を、ファイルの並びの順に返す。"""
    parsed: dict[str, Node] = {}
    lexicon = Lexicon()
    for path, source in sources.items():
        scratch = Lexicon()
        try:
            root = parse(source)
            scratch.collect(root, path)
        except (RecursionError, MemoryError, ValueError):
            # 深く入れ子のファイルと NUL 文字を含むファイルは木を辿れないので飛ばす。
            # 途中まで集めた語彙と項目を残さないため、走査が通ったときだけ全体へ足す
            continue
        lexicon.merge(scratch)
        parsed[path] = root
    found: list[StoreAccess] = []
    for path, root in parsed.items():
        accesses: list[StoreAccess] = []
        try:
            units = list(_functions(root))
            for function, qualified in units:
                touches = [t for road in _ROADS for t in road(function, lexicon)]
                # 保存物の名前ごとに分ける。一つの関数が二つの保存物に触れても鍵は混ざらない
                for name in dict.fromkeys(t.name for t in touches):
                    own = [t for t in touches if t.name == name]
                    sides = tuple(
                        side
                        for role in (StoreRole.WRITE, StoreRole.READ)
                        if (side := _side(role, qualified, path, own)) is not None
                    )
                    accesses.append(
                        StoreAccess(
                            path=path, names=frozenset({name}), pair_owner=None, sides=sides
                        )
                    )
        except (RecursionError, MemoryError, ValueError):
            # 途中まで集めた項目を残さないため、走査が通ったときだけ全体へ足す
            continue
        found.extend(accesses)
    return found
