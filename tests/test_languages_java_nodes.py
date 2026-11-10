import pytest

from jigane.languages import java_nodes
from jigane.languages.base import Visibility, parse
from jigane.languages.java import JavaAdapter
from jigane.preconditions.required import required


def test_type_node_tables_derive_from_one_source() -> None:
    kinds = java_nodes.TYPE_KINDS
    without_annotation = {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
    }
    assert set(java_nodes.TYPE_NODES) == set(kinds)
    assert set(java_nodes.TYPE_NODES_WITHOUT_ANNOTATION) == without_annotation
    assert set(java_nodes.TYPE_NODES) - without_annotation == {"annotation_type_declaration"}


@pytest.mark.parametrize(
    ("modifiers", "expected"),
    [
        ("public ", Visibility.PUBLIC),
        ("protected ", Visibility.PUBLIC),
        ("", Visibility.INTERNAL),
        ("private ", Visibility.FILE),
    ],
)
def test_declared_visibility_reads_the_modifiers(modifiers: str, expected: Visibility) -> None:
    root = parse(JavaAdapter(), f"class A {{ {modifiers}void f() {{}} }}".encode()).root_node
    body = required(root.children[0].child_by_field_name("body"))
    method = body.children[1]
    assert method.type == "method_declaration"
    assert java_nodes.declared_visibility(method, Visibility.INTERNAL) is expected
