from tree_sitter import Node

from jigane.languages import api
from jigane.languages.base import (
    BodyBounds,
    DeclarationSpans,
    Span,
    parse,
    span_of,
    split_declaration,
)
from jigane.languages.java import JavaAdapter

adapter = JavaAdapter()

MULTILINE = "class A {\n    void a(\n        int x) {\n        f();\n    }\n}\n"


def _method(code: str) -> Node:
    root = parse(adapter, code.encode()).root_node
    cls = next(c for c in root.children if c.type == "class_declaration")
    body = cls.child_by_field_name("body")
    assert body is not None
    return next(c for c in body.children if c.type == "method_declaration")


def _split(code: str) -> DeclarationSpans:
    node = _method(code)
    body = node.child_by_field_name("body")
    return split_declaration(node, BodyBounds(body, body) if body is not None else None)


def test_split_declaration_without_body() -> None:
    assert _split("abstract class A {\n    abstract void a();\n}\n") == DeclarationSpans(
        Span(2, 2), Span(2, 2), None
    )


def test_split_declaration_opener_and_closer_on_same_line() -> None:
    assert _split("class A {\n    void a() {}\n}\n") == DeclarationSpans(
        Span(2, 2), Span(2, 2), None
    )


def test_split_declaration_multiline_body() -> None:
    assert _split(MULTILINE) == DeclarationSpans(Span(2, 5), Span(2, 3), Span(4, 5))


def test_split_declaration_opener_and_closer_are_different_nodes() -> None:
    node = _method(MULTILINE)
    body = node.child_by_field_name("body")
    assert body is not None
    bounds = BodyBounds(body.children[0], body.children[-1])
    assert (bounds.opener.type, bounds.closer.type) == ("{", "}")
    assert split_declaration(node, bounds) == DeclarationSpans(Span(2, 5), Span(2, 3), Span(4, 5))


def test_split_declaration_closer_ending_before_opener() -> None:
    node = _method(MULTILINE)
    body = node.child_by_field_name("body")
    assert body is not None
    first_line_node = node.children[0]
    assert first_line_node.end_point.row + 1 == 2
    bounds = BodyBounds(opener=body.children[0], closer=first_line_node)
    assert split_declaration(node, bounds) == DeclarationSpans(Span(2, 5), Span(2, 3), None)


def test_span_of_two_nodes() -> None:
    root = parse(adapter, b"class A {}\n\nclass B {\n}\n").root_node
    first, last = root.children[0], root.children[1]
    assert span_of(first) == Span(1, 1)
    assert span_of(first, last) == Span(1, 4)


def test_helpers_are_exported_from_plugin_api() -> None:
    assert api.span_of is span_of
    assert api.split_declaration is split_declaration
    assert api.BodyBounds is BodyBounds
    assert {"span_of", "split_declaration", "BodyBounds"} <= set(api.__all__)
