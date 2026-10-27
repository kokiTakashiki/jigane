"""通信の宛先が、ローカルか、ローカルの外か、字面で確かめられないかを、同じファイルの中だけで調べる。"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum

from tree_sitter import Node

from jigane.preconditions.required import required

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_NUL = "\0"
_BARE_HOST = re.compile(r"[A-Za-z0-9-]+([.][A-Za-z0-9-]+)+")
_MAX_DEPTH = 4


class Verdict(Enum):
    LOCAL = "local"
    REMOTE = "remote"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class Target:
    verdict: Verdict
    # REMOTE のとき、確かめられたホスト
    host: str = ""


_UNDETERMINED = Target(Verdict.UNDETERMINED)
_LOCAL = Target(Verdict.LOCAL)


@dataclass(frozen=True)
class Shape:
    """言語ごとの節点の型と、同じファイルの宣言の拾い方。"""

    strings: frozenset[str]
    interpolations: frozenset[str]
    identifiers: frozenset[str]
    # 宛先のホストを先頭の要素に持つ入れ物。ホスト名だけの文字列を宛先と読む
    host_containers: frozenset[str]
    # 束縛 (名前, 初期化の式) を返す
    bindings: Callable[[Node], Iterator[tuple[str, Node]]]
    # 式の本体か単一の return を持つメソッド (名前, 返す式) を返す
    methods: Callable[[Node], Iterator[tuple[str, Node]]]
    # 呼び出しの節点から、同じファイルのメソッドの名前になりうる名前を返す
    call_name: Callable[[Node], str | None]
    # 自分の型のメンバーを `self.x` や `Self.x` と書いた参照から、メンバーの名前を返す
    member_name: Callable[[Node], str | None] = lambda _node: None


def _text(node: Node) -> str:
    return required(node.text).decode()


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _root(node: Node) -> Node:
    while node.parent is not None:
        node = node.parent
    return node


def _literal_text(node: Node, shape: Shape) -> str:
    """補間の部分を NUL に置き換えた、文字列のリテラルの字面。"""
    start = node.start_byte
    text = required(node.text)
    parts: list[bytes] = []
    cursor = 0
    for child in _walk(node):
        if child is node or child.type not in shape.interpolations:
            continue
        begin, end = child.start_byte - start, child.end_byte - start
        if begin < cursor:
            continue
        parts.append(text[cursor:begin])
        cursor = end
        parts.append(_NUL.encode())
    parts.append(text[cursor:])
    return b"".join(parts).decode(errors="replace")


def _authority_host(authority: str) -> str:
    """`user@host:port` や `[v6]:port` から、ホストだけを返す。取り出せなければ空文字。"""
    host_and_port = authority.rpartition("@")[2]
    if host_and_port.startswith("["):
        # 角括弧の IPv6。閉じ括弧までがホスト
        closing = host_and_port.find("]")
        return host_and_port[1:closing] if closing > 0 else ""
    return host_and_port.split(":")[0]


def _classify_literal(text: str, *, bare_host_allowed: bool) -> Target | None:
    """文字列の字面から宛先を決める。決められなければ None。"""
    scheme = re.search(r"://([^/?#\s\"]*)", text)
    if scheme is not None:
        host = _authority_host(scheme.group(1)).lower()
        if not host or _NUL in host:
            return _UNDETERMINED
        return _LOCAL if host in LOCAL_HOSTS else Target(Verdict.REMOTE, host)
    bare = text.strip("\"'@$").lower()
    if bare in LOCAL_HOSTS:
        return _LOCAL
    if bare_host_allowed and _BARE_HOST.fullmatch(bare) is not None:
        return Target(Verdict.REMOTE, bare)
    return None


def _combine(targets: list[Target]) -> Target:
    # 宛先の候補が複数あるときは、ローカルと確かめられた場合に限りローカルとする。
    # 一つでも遠隔なら遠隔、遠隔が無く決められないものがあれば決められない
    for verdict in (Verdict.REMOTE, Verdict.UNDETERMINED):
        match = next((t for t in targets if t.verdict is verdict), None)
        if match is not None:
            return match
    return _LOCAL if targets else _UNDETERMINED


def _outermost_strings(node: Node, shape: Shape) -> list[Node]:
    found: list[Node] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in shape.strings:
            found.append(current)
            continue
        stack.extend(current.children)
    return found


def _scan(node: Node, shape: Shape, *, bare_host_allowed: bool) -> Target:
    """式の中の文字列のリテラルから宛先を決める。"""
    targets = [
        t
        for s in _outermost_strings(node, shape)
        if (t := _classify_literal(_literal_text(s, shape), bare_host_allowed=bare_host_allowed))
        is not None
    ]
    return _combine(targets)


def resolve(node: Node, shape: Shape, depth: int = 0) -> Target:
    """引数の式が指す宛先を、同じファイルの中で追って決める。"""
    if depth > _MAX_DEPTH:
        return _UNDETERMINED
    if node.type in shape.strings:
        return _scan(node, shape, bare_host_allowed=depth == 0)
    root = _root(node)
    name = _text(node) if node.type in shape.identifiers else shape.member_name(node)
    if name is not None:
        initializers = [value for n, value in shape.bindings(root) if n == name]
        return _combine([resolve(value, shape, depth + 1) for value in initializers])
    callee = shape.call_name(node)
    if callee is not None:
        returned = [value for n, value in shape.methods(root) if n == callee]
        if returned:
            return _combine([resolve(value, shape, depth + 1) for value in returned])
    host_first = depth == 0 and node.type in shape.host_containers
    return _scan(node, shape, bare_host_allowed=host_first)
