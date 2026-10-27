import pytest
from tree_sitter import Node

from jigane.languages.base import CallSite, Creation, parse
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter
from jigane.preconditions.required import required

adapter = SwiftAdapter()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Foo?", "Foo"),
        ("Foo!", "Foo"),
        ("Foo??", "Foo"),
        ("[Foo]", "Array"),
        ("[Foo]?", "Array"),
        ("[[Foo]]", "Array"),
        ("[String: Foo]", "Dictionary"),
        ("[String: [Foo]]", "Dictionary"),
        ("Box<Foo>", "Box"),
        ("Box<Foo>?", "Box"),
        ("some Drawable", "Drawable"),
        ("any Drawable", "Drawable"),
        ("any Drawable?", "Drawable"),
        ("inout Foo", "Foo"),
        ("@escaping Handler", "Handler"),
        ("Outer.Inner", "Inner"),
        ("Outer<X>.Inner", "Inner"),
    ],
)
def test_swift_annotations_reduce_to_the_index_key(raw: str, expected: str) -> None:
    assert adapter.simple_type_name(raw) == expected


def test_python_and_csharp_annotations_are_unchanged() -> None:
    assert PythonAdapter().simple_type_name("list[Foo]") == "list"
    assert PythonAdapter().simple_type_name("Optional[Foo]") == "Foo"
    assert PythonAdapter().simple_type_name("Foo | None") == "Foo"
    assert CSharpAdapter().simple_type_name("List<Foo>?") == "List"
    assert CSharpAdapter().simple_type_name("global::A.B") == "B"


def _body(source: bytes) -> Node:
    function = parse(adapter, source).root_node.children[0]
    return required(function.child_by_field_name("body"))


def test_generic_construction_is_a_creation_site() -> None:
    body = _body(b"func f() { let a = Array<Int>(); let b = Box<Foo>(value: 1) }")
    sites = [s for s in adapter.call_sites(body, b"") if isinstance(s, CallSite)]
    assert [(s.name, s.is_creation) for s in sites] == [("Array<Int>", True), ("Box<Foo>", True)]
    assert sites[1].arguments[0].name == "value"
    kind = adapter.receiver_of(sites[0].node)
    assert isinstance(kind, Creation)
    assert kind.type_text == "Array<Int>"
