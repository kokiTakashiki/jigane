from pathlib import Path

from review_fakes import changed_segment

from jigane.languages.base import MemberDecl, TypeDecl, parse
from jigane.languages.python import PythonAdapter, module_name
from jigane.sequence.index import TypeKey, build_index
from jigane.sequence.roots import changed_members

FIXTURES = Path(__file__).parent / "fixtures" / "sequence" / "python"
PATH = "pkg/shapes.py"
adapter = PythonAdapter()


def _declarations(path: str = PATH) -> list[TypeDecl | MemberDecl]:
    source = (FIXTURES / path).read_bytes()
    return adapter.type_members(parse(adapter, source).root_node, source, path)


def _member(owner: str, name: str, kind: str | None = None) -> MemberDecl:
    return next(
        d
        for d in _declarations()
        if isinstance(d, MemberDecl)
        and (d.owner, d.name) == (owner, name)
        and (kind is None or d.kind == kind)
    )


def test_module_names() -> None:
    assert module_name("src/jigane/claude.py") == "jigane.claude"
    assert module_name("src/jigane/languages/__init__.py") == "jigane.languages"
    assert module_name("pkg/shapes.py") == "pkg.shapes"


def test_types() -> None:
    types = {d.qualified_name: d for d in _declarations() if isinstance(d, TypeDecl)}
    assert {n: t.kind for n, t in types.items()} == {
        "pkg.shapes": "module",
        "pkg.shapes.Point": "class",
        "pkg.shapes.Base": "class",
        "pkg.shapes.Canvas": "class",
        "pkg.shapes.Canvas.Inner": "class",
    }
    assert types["pkg.shapes"].name == "shapes"
    assert types["pkg.shapes.Canvas"].bases == ("Base",)
    assert types["pkg.shapes.Canvas.Inner"].name == "Inner"
    package = _declarations("pkg/__init__.py")[0]
    assert isinstance(package, TypeDecl)
    assert (package.qualified_name, package.kind) == ("pkg", "module")


def test_top_level_function_is_module_method() -> None:
    make = _member("pkg.shapes", "make")
    assert (make.kind, make.type_annotation) == ("method", '"Canvas"')
    assert [(p.name, p.type_annotation, p.optional) for p in make.parameters] == [
        ("kind", "str", False),
        ("size", "int", True),
    ]
    assert make.body is not None


def test_member_kinds() -> None:
    owner = "pkg.shapes.Canvas"
    assert _member(owner, "Canvas").kind == "constructor"
    assert _member(owner, "title").kind == "property"
    assert _member(owner, "build").kind == "method"
    assert _member(owner, "default").kind == "method"
    assert _member(owner, "draw").kind == "method"
    assert _member("pkg.shapes.Canvas.Inner", "run").kind == "method"
    titles = [d for d in _declarations() if isinstance(d, MemberDecl) and d.name == "title"]
    assert len(titles) == 1


def test_parameters_drop_self_and_cls() -> None:
    owner = "pkg.shapes.Canvas"
    assert [p.name for p in _member(owner, "Canvas").parameters] == ["kind", "scale"]
    assert [p.name for p in _member(owner, "build").parameters] == ["kind"]
    assert _member(owner, "default").parameters == ()
    assert [(p.name, p.type_annotation) for p in _member(owner, "draw").parameters] == [
        ("target", "Base")
    ]


def test_fields() -> None:
    owner = "pkg.shapes.Canvas"
    assert _member("pkg.shapes.Point", "x").type_annotation == "int"
    assert _member("pkg.shapes.Point", "y").kind == "field"
    assert _member(owner, "label").type_annotation == "str"
    assert _member(owner, "count").type_annotation is None
    assert _member(owner, "kind", "field").type_annotation is None
    assert _member(owner, "items").type_annotation == "list[str]"
    assert _member(owner, "scale", "field").kind == "field"


def test_body_of_unwraps_decorated_definition() -> None:
    draw = _member("pkg.shapes.Canvas", "draw")
    assert draw.node.type == "decorated_definition"
    assert draw.body is not None
    assert draw.body.type == "block"
    assert adapter.body_of(draw.node) == draw.body
    assert adapter.body_of(_member("pkg.shapes.Point", "x").node) is None


def test_index_matches_declarations() -> None:
    index = build_index(FIXTURES, [PATH, "pkg/__init__.py"], adapter)
    callables = {m.qualified_name: m for m in index.callables.values()}
    names = (
        "pkg.shapes.make",
        "pkg.shapes.Canvas.draw",
        "pkg.shapes.Canvas.Canvas",
        "pkg.shapes.Canvas.build",
        "pkg.shapes.Canvas.Inner.run",
        "pkg.boot",
    )
    for name in names:
        member = callables[name]
        assert member.declaration is not None, name
        assert member.declaration.body is not None, name
    assert index.find_type("Canvas", PATH) is index.types[TypeKey("pkg.shapes.Canvas")]


def test_changed_members_include_top_level_functions() -> None:
    index = build_index(FIXTURES, [PATH], adapter)
    segments = [
        changed_segment(PATH, 7, 7, "make", "function"),
        changed_segment(PATH, 46, 46, "Canvas.draw"),
    ]
    found = changed_members(index, segments, adapter, include_tests=True)
    assert sorted(m.qualified_name for m in found.values()) == [
        "pkg.shapes.Canvas.draw",
        "pkg.shapes.make",
    ]


def test_property_is_a_callable_only_when_its_body_calls(tmp_path: Path) -> None:
    path = "pkg/props.py"
    (tmp_path / "pkg").mkdir()
    (tmp_path / path).write_text(
        "class Box:\n"
        "    def helper(self) -> int:\n"
        "        return 1\n"
        "\n"
        "    @property\n"
        "    def width(self) -> int:\n"
        "        return self.size.width\n"
        "\n"
        "    @property\n"
        "    def label(self) -> str:\n"
        '        return f"{self.size}x"\n'
        "\n"
        "    @property\n"
        "    def computed(self) -> int:\n"
        "        return self.helper()\n",
        encoding="utf-8",
    )
    index = build_index(tmp_path, [path], adapter)
    names = {m.name for m in index.callables.values()}
    assert {"helper", "computed"} <= names
    assert not {"width", "label"} & names
