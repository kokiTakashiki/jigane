"""Swift の Codable の JSON（`JSONEncoder` の `encode`、`JSONDecoder` の `decode`）の道。

読みは三段に分ける。呼び出しを見つける段、値の型を解く段、触れ方を組み立てる段である。
"""

import re
from collections import defaultdict
from collections.abc import Iterator
from typing import NamedTuple

from tree_sitter import Node

from jigane.languages.base import StoreFormat, StoreRole
from jigane.languages.file_paths_core import text
from jigane.languages.swift_file_paths import _Call
from jigane.languages.swift_store_common import Touch, own_nodes, positional_first, suffix
from jigane.languages.swift_store_lexicon import CodableFields, Lexicon

_JSON_ENCODER = "JSONEncoder()"
_JSON_DECODER = "JSONDecoder()"
_TRY_PREFIX = re.compile(r"^try[?!]?\s+")


class _JsonCall(NamedTuple):
    """見つけた `encode` か `decode` の呼び出しの役割と、ラベルの無い最初の引数の式。"""

    role: StoreRole
    argument: Node


def _json_calls(function: Node) -> Iterator[_JsonCall]:
    """`JSONEncoder()` の `encode` と `JSONDecoder()` の `decode` の呼び出し。"""
    for node in own_nodes(function):
        if node.type != "call_expression":
            continue
        call = _Call(node)
        if call.receiver is None or call.name not in {"encode", "decode"}:
            continue
        receiver = _TRY_PREFIX.sub("", text(call.receiver))
        argument = positional_first(node)
        if argument is None:
            continue
        if call.name == "encode" and receiver == _JSON_ENCODER:
            yield _JsonCall(StoreRole.WRITE, argument)
        elif call.name == "decode" and receiver == _JSON_DECODER:
            yield _JsonCall(StoreRole.READ, argument)


def _declared_type(annotation: Node | None) -> str | None:
    """型の注釈の節点から型の名前。`S?` は `S`、`A.B` は `B`。単純な名前でなければ None。"""
    if annotation is None:
        return None
    if annotation.type == "optional_type":
        annotation = next((c for c in annotation.children if c.type == "user_type"), annotation)
    if annotation.type != "user_type":
        return None
    return text(annotation).rsplit(".", 1)[-1]


def _local_types(function: Node) -> dict[str, str]:
    """同じ関数の引数と `let x: 型` の注釈から、名前に書かれた型。食い違う注釈の名前は捨てる。"""
    seen: defaultdict[str, set[str]] = defaultdict(set)
    for parameter in (c for c in function.children if c.type == "parameter"):
        children = parameter.children
        colon = next((i for i, c in enumerate(children) if c.type == ":"), None)
        if colon is None or colon == 0 or colon + 1 >= len(children):
            continue
        declared = _declared_type(children[colon + 1])
        if declared is not None and children[colon - 1].type == "simple_identifier":
            seen[text(children[colon - 1])].add(declared)
    for node in own_nodes(function):
        if node.type != "property_declaration":
            continue
        pattern = next((c for c in node.children if c.type == "pattern"), None)
        annotation = next((c for c in node.children if c.type == "type_annotation"), None)
        identifier = (
            next((c for c in pattern.children if c.type == "simple_identifier"), None)
            if pattern is not None
            else None
        )
        declared = _declared_type(annotation.children[-1]) if annotation is not None else None
        if identifier is not None and declared is not None:
            seen[text(identifier)].add(declared)
    return {name: next(iter(types)) for name, types in seen.items() if len(types) == 1}


def _encoded_type(argument: Node, local_types: dict[str, str]) -> str | None:
    """`encode` に渡した値の、注釈に書かれた型。識別子でない値と注釈の無い値は None。"""
    if argument.type != "simple_identifier":
        return None
    return local_types.get(text(argument))


def _decoded_type(argument: Node) -> str | None:
    """`decode` に渡した `型.self` の型。メタタイプの形でなければ None。"""
    target = argument.child_by_field_name("target")
    is_metatype = argument.type == "navigation_expression" and suffix(argument) == "self"
    if not is_metatype or target is None:
        return None
    return text(target).rsplit(".", 1)[-1]


def _json_touch(
    role: StoreRole, type_name: str, codable: CodableFields, types: frozenset[str]
) -> Touch:
    return Touch(
        role,
        f"{type_name} の JSON",
        codable.fields_of(type_name),
        frozenset(),
        StoreFormat.JSON,
        types,
    )


def codable_json(function: Node, lexicon: Lexicon) -> list[Touch]:
    touches: list[Touch] = []
    local_types: dict[str, str] | None = None
    for call in _json_calls(function):
        if call.role is StoreRole.WRITE:
            local_types = local_types if local_types is not None else _local_types(function)
            declared = _encoded_type(call.argument, local_types)
            if declared is None:
                continue  # 型の注釈が無い値は型を解決できず、保存物を決められないので数えない
            touches.append(_json_touch(StoreRole.WRITE, declared, lexicon.codable, frozenset()))
        else:
            made = _decoded_type(call.argument)
            if made is None:
                continue  # `型.self` の形でない引数は、読む型を決められないので数えない
            touches.append(_json_touch(StoreRole.READ, made, lexicon.codable, frozenset({made})))
    return touches
