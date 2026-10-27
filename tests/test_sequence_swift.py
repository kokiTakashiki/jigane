from pathlib import Path

from jigane.languages.base import MemberDecl, SequenceAdapter, TypeDecl, parse
from jigane.languages.swift import SwiftAdapter
from jigane.sequence.index import TypeKey, build_index

FIXTURES = Path(__file__).parent / "fixtures" / "sequence" / "swift"
adapter = SwiftAdapter()
indexing_adapter: SequenceAdapter = adapter


def _declarations() -> list[TypeDecl | MemberDecl]:
    source = (FIXTURES / "Shapes.swift").read_bytes()
    return adapter.type_members(parse(adapter, source).root_node, source, "Shapes.swift")


def _types() -> list[TypeDecl]:
    return [d for d in _declarations() if isinstance(d, TypeDecl)]


def _member(owner: str, name: str) -> MemberDecl:
    return next(
        d
        for d in _declarations()
        if isinstance(d, MemberDecl) and (d.owner, d.name) == (owner, name)
    )


def test_type_kinds_and_bases() -> None:
    types = {t.qualified_name: t for t in reversed(_types())}
    assert {n: t.kind for n, t in types.items()} == {
        "Drawable": "interface",
        "Base": "class",
        "Canvas": "class",
        "Canvas.Inner": "struct",
        "Wrapper": "struct",
        "Mode": "enum",
        "Counter": "class",
    }
    assert types["Drawable"].bases == ("AnyObject",)
    assert types["Canvas"].bases == ("Base", "Drawable")
    assert types["Canvas.Inner"].name == "Inner"
    assert types["Wrapper"].bases == ("UIViewRepresentable",)
    assert types["Mode"].bases == ("Int",)


def test_extension_is_returned_as_its_target_type() -> None:
    canvases = [t for t in _types() if t.name == "Canvas"]
    assert len(canvases) == 2
    assert canvases[1].line > canvases[0].line
    assert _member("Canvas", "extra").type_annotation == "Int"


def test_extension_is_merged_in_index() -> None:
    index = build_index(FIXTURES, ["Shapes.swift"], indexing_adapter)
    assert {"draw", "extra", "move", "Canvas"} <= set(index.types[TypeKey("Canvas")].members)


def test_method_parameters_keep_external_and_internal_names() -> None:
    method = _member("Canvas", "move")
    assert [
        (p.external_name, p.name, p.type_annotation, p.optional) for p in method.parameters
    ] == [
        ("_", "dx", "Int", False),
        ("by", "step", "Int", False),
        (None, "scale", "Double", True),
    ]
    draw = _member("Canvas", "draw")
    assert (draw.parameters[0].external_name, draw.parameters[0].name) == ("in", "rect")
    assert draw.type_annotation == "Bool"
    assert draw.selector == "draw(in:)"


def test_initializer_is_a_constructor_named_after_the_type() -> None:
    init = _member("Canvas", "Canvas")
    assert init.kind == "constructor"
    assert init.selector == "init(title:)"
    assert init.body is not None


def test_properties() -> None:
    size = _member("Canvas", "size")
    assert (size.kind, size.type_annotation) == ("field", "Int")
    assert _member("Canvas", "title").type_annotation == "String?"
    assert _member("Canvas", "doubled").kind == "property"
    assert _member("Drawable", "size").kind == "property"
    assert _member("Counter", "count").type_annotation is None


def test_body_of() -> None:
    assert adapter.body_of(_member("Canvas", "draw").node) is not None
    assert adapter.body_of(_member("Drawable", "draw").node) is None
    assert adapter.body_of(_member("Canvas", "size").node) is None
    doubled = adapter.body_of(_member("Canvas", "doubled").node)
    assert doubled is not None
    assert doubled.type == "computed_property"
    explicit = adapter.body_of(_member("Canvas", "explicit").node)
    assert explicit is not None
    assert explicit.type == "computed_getter"


def test_initializer_is_shown_as_init_but_resolved_by_the_type_name() -> None:
    index = build_index(FIXTURES, ["Shapes.swift"], indexing_adapter)
    init = index.types[TypeKey("Canvas")].members["Canvas"][0]
    assert init.qualified_name == "Canvas.init"
    assert init.name == "Canvas"


def test_declaration_is_matched_by_selector() -> None:
    index = build_index(FIXTURES, ["Shapes.swift"], indexing_adapter)
    draw = index.types[TypeKey("Canvas")].members["draw"][0].declaration
    assert draw is not None
    assert draw.qualified_name == "Canvas.draw(in:)"
    init = index.types[TypeKey("Canvas")].members["Canvas"][0].declaration
    assert init is not None
    assert init.qualified_name == "Canvas.init(title:)"
    assert index.types[TypeKey("Wrapper")].members["makeUIView"][0].declaration is not None
    assert index.types[TypeKey("Wrapper")].bases == ("UIViewRepresentable",)
