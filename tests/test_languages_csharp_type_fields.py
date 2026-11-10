import pytest
from tree_sitter import Node

from jigane.languages.base import TypeFieldReader, TypeFields
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.csharp_type_fields import type_fields_of


def _read(source: str) -> list[TypeFields]:
    return CSharpAdapter().type_fields(source, "a.cs")


def _shape(source: str) -> list[tuple[str, int, tuple[str, ...]]]:
    return [(t.name, t.line, t.fields) for t in _read(source)]


def test_csharp_adapter_satisfies_the_type_field_reader() -> None:
    assert isinstance(CSharpAdapter(), TypeFieldReader)


def test_instance_fields_and_auto_properties_are_fields() -> None:
    source = (
        "class Book\n{\n    private int a, b;\n    public string Title { get; set; }\n"
        "    public int Pages { get; init; } = 3;\n    public readonly int Isbn;\n}\n"
    )
    assert _shape(source) == [("Book", 1, ("a", "b", "Title", "Pages", "Isbn"))]


def test_static_const_event_and_bodied_properties_are_not_fields() -> None:
    source = (
        "class Book\n{\n    static int s;\n    const int C = 1;\n"
        "    public static int T { get; set; }\n"
        "    public int Q => 1;\n    public int R { get { return 1; } }\n"
        "    public int S { get => 1; }\n    public event System.Action E;\n    int kept;\n}\n"
    )
    assert _shape(source) == [("Book", 1, ("kept",))]


def test_record_positional_parameters_and_body_properties_are_fields() -> None:
    source = (
        "public record Q(int Z, string Y)\n{\n    public int W { get; init; }\n}\n"
        "public record struct R(int X);\n"
    )
    assert _shape(source) == [("Q", 1, ("Z", "Y", "W")), ("R", 5, ("X",))]


def test_primary_constructor_parameters_of_a_class_are_not_fields() -> None:
    assert _shape("class C(int x) { int y; }\n") == [("C", 1, ("y",))]


def test_enum_members_are_fields() -> None:
    assert _shape("enum Kind\n{\n    Draft,\n    Review = 2,\n}\n") == [
        ("Kind", 1, ("Draft", "Review"))
    ]


def test_interface_and_delegate_are_not_returned() -> None:
    source = "interface I { int X { get; set; } }\ndelegate void D(int x);\n"
    assert _read(source) == []


def test_duplicates_are_removed_keeping_declaration_order() -> None:
    source = "record P(int X, int Y) { public int X { get; init; } public int Z { get; set; } }\n"
    assert _shape(source) == [("P", 1, ("X", "Y", "Z"))]


def test_type_without_fields_is_not_returned() -> None:
    assert _read("class Empty { void M() { } }\n") == []


def test_partial_declarations_are_returned_separately() -> None:
    source = "partial class K { int a; }\npartial class K { int b; }\n"
    assert _shape(source) == [("K", 1, ("a",)), ("K", 2, ("b",))]


def test_types_in_namespaces_and_preprocessor_branches_are_top_level() -> None:
    source = (
        "#if DEBUG\nnamespace N\n{\n    class A { int a; }\n}\n#endif\n"
        "namespace M;\nclass B { int b; }\n"
    )
    assert _shape(source) == [("A", 4, ("a",)), ("B", 8, ("b",))]


def test_nested_types_are_not_read() -> None:
    source = "class Outer\n{\n    int o;\n    class Inner { int i; }\n}\n"
    assert _shape(source) == [("Outer", 1, ("o",))]


@pytest.mark.parametrize("error", [RecursionError, MemoryError, ValueError])
def test_file_whose_tree_cannot_be_built_is_dropped(error: type[Exception]) -> None:
    def fail() -> Node:
        raise error

    assert type_fields_of(fail, "a.cs") == []
