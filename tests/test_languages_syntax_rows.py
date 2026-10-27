import pytest
from tree_sitter import Node

from jigane.languages.base import (
    LanguageAdapter,
    comment_nodes,
    import_rows,
    leaves_and_comments,
    parse,
)
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.java import JavaAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter

SAMPLES: dict[str, tuple[LanguageAdapter, str]] = {
    "python": (
        PythonAdapter(),
        (
            "# top\nimport os\nfrom a import (\n    b,\n    c,\n)\n\n"
            "def f():\n    import json  # inline\n    x = 1  # trailing\n    return x\n"
        ),
    ),
    "java": (
        JavaAdapter(),
        (
            "package p;\n// line\nimport a.b.C;\nimport static a.b.*;\n"
            "/* block\n   comment */\nclass A {\n    /** doc */\n    void f() {} // tail\n}\n"
        ),
    ),
    "csharp": (
        CSharpAdapter(),
        (
            "// top\nglobal using System;\nusing X = System.Y;\n"
            "class A {\n    /* b */\n    void F() {\n        using (var a = b) { }\n"
            "        using var c = d;\n    }\n}\n"
        ),
    ),
    "swift": (
        SwiftAdapter(),
        (
            "// top\nimport Foundation\n@testable import X\n/* multi\n   line */\n"
            "class A {\n    func f() {} // tail\n}\n"
        ),
    ),
}


def old_judgment_map_comments(adapter: LanguageAdapter, root: Node) -> list[Node]:
    # 共通の関数に置き換える前の、judgment_map の葉の収集
    found: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.child_count == 0 or node.type in adapter.comment_node_types:
            if node.type in adapter.comment_node_types:
                found.append(node)
        else:
            stack.extend(reversed(node.children))
    return found


def old_detector_comments(root: Node, types: frozenset[str]) -> list[Node]:
    # 共通の関数に置き換える前の、flaky の検出の収集
    found: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in types:
            found.append(node)
        stack.extend(node.children)
    return found


def old_repeats_comments(adapter: LanguageAdapter, root: Node) -> list[Node]:
    # 共通の関数に置き換える前の、policy_repeats の収集
    found: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type not in adapter.comment_node_types:
            stack.extend(node.children)
            continue
        found.append(node)
    return found


def key(nodes: list[Node]) -> list[tuple[int, int, int, int]]:
    return sorted((n.start_byte, n.end_byte, n.start_point.row, n.end_point.row) for n in nodes)


@pytest.mark.parametrize("language", SAMPLES)
def test_comment_nodes_match_the_three_former_traversals(language: str) -> None:
    adapter, code = SAMPLES[language]
    root = parse(adapter, code.encode()).root_node
    shared = key(comment_nodes(adapter, root))
    assert shared
    assert shared == key(old_judgment_map_comments(adapter, root))
    assert shared == key(old_detector_comments(root, adapter.comment_node_types))
    assert shared == key(old_repeats_comments(adapter, root))


@pytest.mark.parametrize("language", SAMPLES)
def test_comment_nodes_are_in_document_order(language: str) -> None:
    adapter, code = SAMPLES[language]
    nodes = comment_nodes(adapter, parse(adapter, code.encode()).root_node)
    assert [n.start_byte for n in nodes] == sorted(n.start_byte for n in nodes)


def test_leaves_and_comments_carry_ancestor_types_and_skip_comment_children() -> None:
    adapter, code = SAMPLES["python"]
    root = parse(adapter, code.encode()).root_node
    items = list(leaves_and_comments(adapter, root))
    comments = [(n, a) for n, a in items if n.type in adapter.comment_node_types]
    assert comments
    assert all(ancestors[0] == "module" for _, ancestors in items if ancestors)
    leaf_bytes = [n.start_byte for n, _ in items]
    assert leaf_bytes == sorted(leaf_bytes)


def rows(language: str) -> set[int]:
    adapter, code = SAMPLES[language]
    return import_rows(adapter, parse(adapter, code.encode()).root_node)


def test_python_import_rows_include_multiline_and_nested_imports() -> None:
    assert rows("python") == {2, 3, 4, 5, 6, 9}


def test_java_import_rows_exclude_package() -> None:
    assert rows("java") == {3, 4}


def test_csharp_import_rows_are_directives_not_using_statements() -> None:
    assert rows("csharp") == {2, 3}


def test_swift_import_rows_include_attributed_imports() -> None:
    assert rows("swift") == {2, 3}
