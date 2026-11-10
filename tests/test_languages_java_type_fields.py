import pytest
from tree_sitter import Node

from jigane.languages.base import TypeFieldReader, TypeFields
from jigane.languages.java import JavaAdapter
from jigane.languages.java_type_fields import type_fields_of


def _read(source: str) -> list[TypeFields]:
    return JavaAdapter().type_fields(source, "A.java")


def _shape(source: str) -> list[tuple[str, int, tuple[str, ...]]]:
    return [(t.name, t.line, t.fields) for t in _read(source)]


def test_java_adapter_satisfies_the_type_field_reader() -> None:
    assert isinstance(JavaAdapter(), TypeFieldReader)


def test_instance_fields_are_fields_by_name() -> None:
    source = (
        "class Book\n{\n    private int a, b;\n    public String title;\n"
        "    final int isbn = 1;\n}\n"
    )
    assert _shape(source) == [("Book", 1, ("a", "b", "title", "isbn"))]


def test_static_fields_and_methods_are_not_fields() -> None:
    source = (
        "class Book\n{\n    static int s;\n    public static final int C = 1;\n"
        "    void m() { }\n    int kept;\n}\n"
    )
    assert _shape(source) == [("Book", 1, ("kept",))]


def test_record_components_and_body_fields_are_fields() -> None:
    source = "record Q(int z, String y)\n{\n    static int s;\n}\nrecord R(int x) { }\n"
    assert _shape(source) == [("Q", 1, ("z", "y")), ("R", 5, ("x",))]


def test_enum_constants_and_instance_fields_after_semicolon_are_fields() -> None:
    source = (
        "enum Kind\n{\n    DRAFT, REVIEW(2);\n    int rank;\n    static int s;\n"
        "    Kind() { }\n}\n"
    )
    assert _shape(source) == [("Kind", 1, ("DRAFT", "REVIEW", "rank"))]


def test_interface_and_annotation_type_are_not_returned() -> None:
    source = "interface I { int C = 1; }\n@interface An { int v(); }\n"
    assert _read(source) == []


def test_duplicates_are_removed_keeping_declaration_order() -> None:
    source = "record P(int x, int y) { int x; int z; }\n"
    assert _shape(source) == [("P", 1, ("x", "y", "z"))]


def test_type_without_fields_is_not_returned() -> None:
    assert _read("class Empty { void m() { } }\n") == []


def test_nested_types_are_not_read() -> None:
    source = "class Outer\n{\n    int o;\n    class Inner { int i; }\n    enum E { X }\n}\n"
    assert _shape(source) == [("Outer", 1, ("o",))]


def test_top_level_types_follow_package_in_document_order() -> None:
    source = "package p;\nclass A { int a; }\nenum B { X }\n"
    assert _shape(source) == [("A", 2, ("a",)), ("B", 3, ("X",))]


@pytest.mark.parametrize("error", [RecursionError, MemoryError, ValueError])
def test_file_whose_tree_cannot_be_built_is_dropped(error: type[Exception]) -> None:
    def fail() -> Node:
        raise error

    assert type_fields_of(fail, "A.java") == []
