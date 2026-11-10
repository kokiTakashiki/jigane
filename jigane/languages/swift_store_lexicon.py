"""Swift の保存物の道へ渡す語彙（鍵の定数、列挙型の case、Codable の型の項目）。

全ファイルから先に集め、道は引くだけにする。
"""

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from tree_sitter import Node

from jigane.languages.file_paths_core import text
from jigane.languages.swift_nodes import string_literal
from jigane.languages.swift_store_common import (
    TYPE_NODES,
    Touch,
    ancestors,
    suffix,
    type_name,
)
from jigane.languages.swift_type_fields import type_fields_of
from jigane.languages.value_set_core import walk_nodes

_BODY_NODES = frozenset({"class_body", "enum_class_body"})
_TOP_LEVEL = ""
_CODABLE = frozenset({"Codable", "Encodable", "Decodable"})
_CODABLE_KEYWORDS = frozenset({"struct", "class"})
_ASSIGNMENT_PARTS = 2  # 「=」と生の値


class CodableFields:
    """Codable の型の項目。同じ名前の定義が複数あって一つに決められない型は引けない。"""

    def __init__(self) -> None:
        self.fields: defaultdict[str, set[tuple[str, ...]]] = defaultdict(set)

    def merge(self, other: "CodableFields") -> None:
        for name, definitions in other.fields.items():
            self.fields[name] |= definitions

    def fields_of(self, type_name: str) -> frozenset[str]:
        """型の項目。型がまだ集まっていないか、定義が食い違うなら空。"""
        definitions = self.fields.get(type_name, set())
        return frozenset(next(iter(definitions))) if len(definitions) == 1 else frozenset()

    def collect(self, root: Node, path: str) -> None:
        by_line = {(t.name, t.line): t.fields for t in type_fields_of(root, path)}
        for node in root.children:
            name = type_name(node) if node.type in TYPE_NODES else None
            if name is None or not _is_codable(node):
                continue
            fields = by_line.get((name, node.start_point.row + 1))
            if fields:
                self.fields[name].add(fields)


class Vocabulary:
    """鍵の語彙（定数と列挙型の case）。同じ名前の定義が複数あり決められないものは引けない。"""

    def __init__(self) -> None:
        self.constants: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
        self.cases: defaultdict[tuple[str, str], set[str]] = defaultdict(set)

    def merge(self, other: "Vocabulary") -> None:
        for mine, theirs in ((self.constants, other.constants), (self.cases, other.cases)):
            for key, values in theirs.items():
                mine[key] |= values

    def collect(self, root: Node) -> None:
        for node in walk_nodes(root):
            if node.type == "property_declaration" and node.parent is not None:
                if node.parent.type == "source_file":
                    self._add_constant(node, _TOP_LEVEL)
            elif node.type in TYPE_NODES:
                self._collect_type(node)

    def _collect_type(self, node: Node) -> None:
        name = type_name(node)
        body = next((c for c in node.children if c.type in _BODY_NODES), None)
        if name is None or body is None:
            return
        for member in body.children:
            if member.type == "property_declaration":
                self._add_constant(member, name)
            elif member.type == "enum_entry" and _is_string_enum(node):
                self._add_cases(member, name)

    def _add_constant(self, node: Node, owner: str) -> None:
        declared = node.child_by_field_name("name")
        identifier = declared.child_by_field_name("bound_identifier") if declared else None
        value = node.child_by_field_name("value")
        is_let = any(c.type == "value_binding_pattern" and text(c) == "let" for c in node.children)
        modifiers = next((c for c in node.children if c.type == "modifiers"), None)
        is_static = modifiers is not None and "static" in text(modifiers).split()
        # 型の外の `let` は定数、型の中は `static let` だけが型の定数
        if identifier is None or value is None or not is_let or (owner and not is_static):
            return
        string = string_literal(value)
        if string is not None:
            self.constants[(owner, text(identifier))].add(string)

    def _add_cases(self, entry: Node, owner: str) -> None:
        children = entry.children
        for index, child in enumerate(children):
            if child.type != "simple_identifier":
                continue
            following = children[index + 1 : index + 3]
            if len(following) == _ASSIGNMENT_PARTS and following[0].type == "=":
                value = string_literal(following[1])
                if value is not None:
                    self.cases[(owner, text(child))].add(value)
            else:
                # 生の値を書かない case の生の値は、case の名前
                self.cases[(owner, text(child))].add(text(child))

    def resolve(self, expression: Node) -> tuple[str, str] | None:
        """鍵の式から (文字列, 語彙の名前)。解けなければ None。"""
        string = string_literal(expression)
        if string is not None:
            return (string, "")
        if expression.type == "simple_identifier":
            return self._bare(expression)
        if expression.type == "navigation_expression":
            return self._navigation(expression)
        return None

    def _navigation(self, expression: Node) -> tuple[str, str] | None:
        member = suffix(expression)
        target = expression.child_by_field_name("target")
        if member is None or target is None:
            return None
        if target.type == "simple_identifier":
            return self._pick(self.constants, (text(target), member), text(target))
        owner = target.child_by_field_name("target")
        case = suffix(target) if target.type == "navigation_expression" else None
        is_raw_value = member == "rawValue" and owner is not None and case is not None
        if is_raw_value and owner is not None and owner.type == "simple_identifier":
            return self._pick(self.cases, (text(owner), case or ""), text(owner))
        return None

    def _bare(self, identifier: Node) -> tuple[str, str] | None:
        owners = [type_name(a) for a in ancestors(identifier) if a.type in TYPE_NODES]
        for owner in [*owners, _TOP_LEVEL]:
            if owner is not None and (owner, text(identifier)) in self.constants:
                return self._pick(self.constants, (owner, text(identifier)), owner)
        return None

    @staticmethod
    def _pick(
        table: Mapping[tuple[str, str], set[str]], key: tuple[str, str], label: str
    ) -> tuple[str, str] | None:
        values = table.get(key, set())
        # 複数の定義が食い違えば推測せず捨てる
        return (next(iter(values)), label) if len(values) == 1 else None


def _is_string_enum(node: Node) -> bool:
    return any(c.type == "enum" for c in node.children) and any(
        c.type == "inheritance_specifier" and text(c) == "String" for c in node.children
    )


def _is_codable(node: Node) -> bool:
    return any(c.type in _CODABLE_KEYWORDS for c in node.children) and any(
        c.type == "inheritance_specifier" and text(c) in _CODABLE for c in node.children
    )


@dataclass
class Lexicon:
    """道へ渡す、全ファイルから集めた語彙。ファイルごとの一時の集めも同じ型で持つ。"""

    keys: Vocabulary = field(default_factory=Vocabulary)
    codable: CodableFields = field(default_factory=CodableFields)

    def merge(self, other: "Lexicon") -> None:
        self.keys.merge(other.keys)
        self.codable.merge(other.codable)

    def collect(self, root: Node, path: str) -> None:
        self.keys.collect(root)
        self.codable.collect(root, path)


Road = Callable[[Node, Lexicon], list[Touch]]
