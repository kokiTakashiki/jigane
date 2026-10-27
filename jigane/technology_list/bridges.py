"""変更したファイルの中の橋渡しを、橋渡しの表の見つけ方に当たる箇所として挙げる。"""

import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from tree_sitter import Node

import jigane.languages
from jigane.git.tree_files import read_tree_bytes
from jigane.languages import registry
from jigane.languages.base import ImportAdapter, LanguageAdapter, parse
from jigane.reviews.lifecycle import Review

BRIDGES_DIR = Path(jigane.languages.__file__).parent / "bridges"


@dataclass(frozen=True)
class Entry:
    name: str
    source: str
    target: str
    via: tuple[str, ...]
    body_members: tuple[str, ...]


@cache
def load_table(language: str) -> dict[str, Entry]:
    """名前から表の項目への対応。表のファイルが無い言語では空。"""
    file = BRIDGES_DIR / f"{language}.toml"
    if not file.is_file():
        # 橋渡しの構文を持たない言語は表のファイルを置かない
        return {}
    return {
        raw["name"]: Entry(
            raw["name"],
            raw["from"],
            raw["to"],
            tuple(raw["via"]),
            tuple(raw.get("body_members", ())),
        )
        for raw in tomllib.loads(file.read_text(encoding="utf-8"))["bridge"]
    }


def _lines(node: Node) -> int:
    return node.end_point.row - node.start_point.row + 1


@dataclass(frozen=True)
class _Place:
    path: str
    line: int


def _found(
    entry: Entry,
    via: str,
    place: _Place,
    name: str,
    *,
    declaration: Node | None,
    members: int | None = None,
    body_lines: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "path": place.path,
        "line": place.line,
        "type_lines": None if declaration is None else _lines(declaration),
        "members": members,
        "bridge": entry.name,
        "from": entry.source,
        "to": entry.target,
        "via": via,
        "body_lines": body_lines,
    }


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(node: Node) -> str:
    return node.text.decode("utf-8", errors="replace") if node.text is not None else ""


def _child(node: Node, *types: str) -> Node | None:
    return next((c for c in node.children if c.type in types), None)


def _swift_type_identifier(node: Node) -> str:
    # ジェネリクスの型引数と修飾は落とし、最後の型の名前を使う
    identifiers = [c for c in node.children if c.type == "type_identifier"]
    return _text(identifiers[-1]) if identifiers else _text(node)


def _swift_type_name(declaration: Node) -> str:
    # extension は宣言の名前が user_type に入る
    name = _child(declaration, "type_identifier", "user_type")
    return "" if name is None else _swift_type_identifier(name)


def _swift_body(declaration: Node) -> Node | None:
    return _child(declaration, "class_body", "enum_class_body")


def _swift_members(declaration: Node) -> int:
    body = _swift_body(declaration)
    return 0 if body is None else sum("comment" not in c.type for c in body.named_children)


def _swift_body_lines(declaration: Node, names: tuple[str, ...]) -> dict[str, int]:
    body = _swift_body(declaration)
    if body is None:
        return {}
    lines: dict[str, int] = {}
    for member in body.named_children:
        if member.type != "function_declaration":
            continue
        name = _child(member, "simple_identifier")
        function_body = _child(member, "function_body")
        if name is not None and function_body is not None and _text(name) in names:
            lines[_text(name)] = _lines(function_body)
    return lines


def _swift_enclosing(node: Node) -> Node | None:
    """最も内側の型。型の外なら最も内側の関数。どちらも無ければ None。"""
    function: Node | None = None
    current = node.parent
    while current is not None:
        if current.type == "class_declaration":
            return current
        if function is None and current.type in ("function_declaration", "init_declaration"):
            function = current
        current = current.parent
    return function


def _swift_enclosing_name(declaration: Node) -> str:
    if declaration.type == "class_declaration":
        return _swift_type_name(declaration)
    if declaration.type == "init_declaration":
        return "init"
    name = _child(declaration, "simple_identifier")
    return "" if name is None else _text(name)


def _swift(root: Node, path: str, table: dict[str, Entry]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for node in _walk(root):
        if node.type == "class_declaration":
            for specifier in (c for c in node.children if c.type == "inheritance_specifier"):
                user_type = _child(specifier, "user_type")
                entry = None if user_type is None else table.get(_swift_type_identifier(user_type))
                if entry is None or "base" not in entry.via:
                    # 表に無い基底の型は橋渡しではない
                    continue
                found.append(
                    _found(
                        entry,
                        "base",
                        _Place(path, node.start_point.row + 1),
                        _swift_type_name(node),
                        declaration=node,
                        members=_swift_members(node),
                        body_lines=_swift_body_lines(node, entry.body_members) or None,
                    )
                )
        elif node.type == "call_expression" and node.children:
            callee = node.children[0]
            entry = table.get(_text(callee)) if callee.type == "simple_identifier" else None
            if entry is None or "creation" not in entry.via:
                # 表に無い名前の呼び出しは橋渡しではない
                continue
            enclosing = _swift_enclosing(node)
            is_type = enclosing is not None and enclosing.type == "class_declaration"
            found.append(
                _found(
                    entry,
                    "creation",
                    _Place(path, node.start_point.row + 1),
                    "(ファイル直下)" if enclosing is None else _swift_enclosing_name(enclosing),
                    declaration=enclosing,
                    members=_swift_members(enclosing)
                    if enclosing is not None and is_type
                    else None,
                )
            )
    return found


def _csharp(root: Node, path: str, table: dict[str, Entry]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for node in _walk(root):
        if node.type != "attribute" or not node.children:
            continue
        # System.Runtime.InteropServices.DllImport のような修飾は最後の名前だけを見る
        written = _text(node.children[0]).rsplit(".", 1)[-1].removesuffix("Attribute")
        entry = table.get(written)
        if entry is None or "attribute" not in entry.via:
            # 表に無い属性は橋渡しではない
            continue
        owner = node.parent.parent if node.parent is not None else None
        name = None if owner is None else _child(owner, "identifier")
        found.append(
            _found(
                entry,
                "attribute",
                _Place(path, node.start_point.row + 1),
                "" if name is None else _text(name),
                declaration=owner,
            )
        )
    return found


def _java(root: Node, path: str, table: dict[str, Entry]) -> list[dict[str, Any]]:
    entry = table.get("native")
    if entry is None:
        # 表に native の項目が無ければ、この言語の橋渡しは挙げない
        return []
    found: list[dict[str, Any]] = []
    for node in _walk(root):
        if node.type != "method_declaration":
            continue
        modifiers = _child(node, "modifiers")
        if modifiers is None or not any(c.type == "native" for c in modifiers.children):
            # native でないメソッドは Java の中で完結する
            continue
        name = _child(node, "identifier")
        found.append(
            _found(
                entry,
                "modifier",
                _Place(path, node.start_point.row + 1),
                "" if name is None else _text(name),
                declaration=node,
            )
        )
    return found


def _python(
    adapter: ImportAdapter, root: Node, source: bytes, path: str, table: dict[str, Entry]
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in adapter.imports(root, source):
        if item.module.startswith("."):
            # 相対の import は同じリポジトリの中を指す
            continue
        entry = table.get(item.module.split(".")[0])
        if entry is not None and "import" in entry.via:
            found.append(
                _found(entry, "import", _Place(path, item.line), entry.name, declaration=None)
            )
    return found


_DETECTORS: dict[str, Callable[[Node, str, dict[str, Entry]], list[dict[str, Any]]]] = {
    "swift": _swift,
    "csharp": _csharp,
    "java": _java,
}


def _detect(
    adapter: LanguageAdapter, root: Node, source: bytes, path: str, table: dict[str, Entry]
) -> list[dict[str, Any]]:
    if adapter.name in _DETECTORS:
        return _DETECTORS[adapter.name](root, path, table)
    if isinstance(adapter, ImportAdapter):
        # import で見つける言語。表の項目が import を見つけ方にするのはこの経路だけ
        return _python(adapter, root, source, path, table)
    return []


def collect(review: Review) -> list[dict[str, Any]]:
    """対象のファイルの橋渡しを、パスと行の順に返す。"""
    root = Path(review.repository_root)
    found: list[dict[str, Any]] = []
    for path in review.paths:
        adapter = registry.adapter_for(path)
        table = {} if adapter is None else load_table(adapter.name)
        if adapter is None or not table:
            # 表の無い言語のファイルに橋渡しは無い
            continue
        source = read_tree_bytes(root, path)
        if source is None:
            continue
        found.extend(_detect(adapter, parse(adapter, source).root_node, source, path, table))
    return sorted(found, key=lambda b: (b["path"], b["line"]))
