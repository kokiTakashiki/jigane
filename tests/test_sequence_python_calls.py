from pathlib import Path

import pytest
from tree_sitter import Node

from jigane.languages.base import Call as CallKind
from jigane.languages.base import (
    CallSite,
    DeferredSite,
    Member,
    Name,
    Paren,
    Self,
    SequenceAdapter,
    Super,
    Unknown,
    parse,
)
from jigane.languages.python import PythonAdapter
from jigane.sequence.index import TypeKey, build_index
from jigane.sequence.resolve import External, Resolution, Resolved, Unresolved, extract_calls

adapter = PythonAdapter()


def _body(code: str) -> tuple[Node, bytes]:
    source = code.encode()
    root = parse(adapter, source).root_node
    function = root.children[0]
    body = adapter.body_of(function)
    assert body is not None
    return body, source


def _sites(code: str) -> list[CallSite | DeferredSite]:
    body, source = _body(code)
    return adapter.call_sites(body, source)


def test_python_adapter_is_sequence_adapter() -> None:
    assert isinstance(adapter, SequenceAdapter)


def test_calls_in_evaluation_order() -> None:
    sites = _sites("def f():\n    a(b(), c())\n    d()\n")
    assert [s.name for s in sites if isinstance(s, CallSite)] == ["b", "c", "a", "d"]
    ends = [s.end for s in sites]
    assert ends == sorted(ends)


def test_arguments_carry_keyword_names() -> None:
    (site,) = _sites("def f():\n    g(1, k=2)\n")
    assert isinstance(site, CallSite)
    assert [a.name for a in site.arguments] == [None, "k"]
    assert not site.is_creation


def test_super_call_is_not_an_arrow_but_receiver_is_super() -> None:
    sites = _sites("def f(self):\n    super().__init__()\n")
    assert [s.name for s in sites if isinstance(s, CallSite)] == ["__init__"]
    (site,) = sites
    assert isinstance(site, CallSite)
    assert site.receiver is not None
    assert isinstance(adapter.receiver_of(site.receiver), Super)


def test_receiver_kinds() -> None:
    sites = _sites(
        "def f(self, cls):\n    self.a()\n    claude.run()\n    x.y.z()\n    h().k()\n"
        "    (m).n()\n"
    )
    kinds = [
        adapter.receiver_of(s.receiver) for s in sites if isinstance(s, CallSite) and s.receiver
    ]
    assert isinstance(kinds[0], Self)
    assert kinds[1] == Name("claude")
    assert isinstance(kinds[2], Member)
    assert kinds[2].name == "y"
    assert isinstance(kinds[3], CallKind)
    assert isinstance(kinds[4], Paren)
    assert isinstance(adapter.receiver_of(_body("def f():\n    1 + 2\n")[0].children[0]), Unknown)


def test_cls_is_self() -> None:
    (site,) = _sites("def f(cls):\n    cls.make()\n")
    assert isinstance(site, CallSite)
    assert site.receiver is not None
    assert isinstance(adapter.receiver_of(site.receiver), Self)


def test_lambda_body_calls_are_deferred() -> None:
    sites = _sites("def f(x):\n    g = lambda q: q.go(helper(q))\n    run()\n")
    assert [s.name for s in sites if isinstance(s, CallSite)] == ["run"]
    deferred = [s for s in sites if isinstance(s, DeferredSite)]
    assert [d.kind for d in deferred] == ["lambda"]
    assert [c.name for c in deferred[0].calls] == ["helper", "go"]


def test_method_reference_arguments_are_method_groups() -> None:
    sites = _sites("def f(self):\n    self.m(self.handler, cb=other.fn)\n")
    groups = [s for s in sites if isinstance(s, DeferredSite)]
    assert sorted((g.name, g.kind) for g in groups) == [
        ("fn", "method_group"),
        ("handler", "method_group"),
    ]


def test_inner_function_body_is_not_inline() -> None:
    sites = _sites("def f():\n    def inner():\n        hidden()\n    shown()\n")
    assert [s.name for s in sites if isinstance(s, CallSite)] == ["shown"]


def test_locals_declaration_forms() -> None:
    body, _ = _body(
        "def f(p):\n"
        "    a: Optional[Foo] = make()\n"
        "    b = Bar()\n"
        "    c: int\n"
        "    d, e = pair()\n"
        "    for i, (j, k) in items:\n"
        "        pass\n"
        "    with open(p) as fh, other as (u, v):\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except (A, B) as err:\n"
        "        pass\n"
        "    def inner():\n"
        "        z = 1\n"
        "    g = lambda q, r=1, *s: q\n"
    )
    scanned = adapter.locals_of(body)
    by_name = {v.name: v for v in scanned.variables}
    assert by_name["a"].type_annotation == "Optional[Foo]"
    assert by_name["a"].initializer is not None
    assert by_name["b"].type_annotation is None
    assert by_name["b"].initializer is not None
    assert by_name["b"].initializer.type == "call"
    assert by_name["c"].type_annotation == "int"
    assert by_name["c"].initializer is None
    for name in ("d", "e", "i", "j", "k", "fh", "u", "v", "err"):
        assert name in by_name, name
        assert by_name[name].type_annotation is None
    assert "z" not in by_name
    assert scanned.functions == frozenset({"inner"})
    assert scanned.lambda_parameters == frozenset({"q", "r", "s"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Foo", "Foo"),
        ("Optional[Foo]", "Foo"),
        ("typing.Optional[Foo]", "Foo"),
        ("Foo | None", "Foo"),
        ("None | Foo", "Foo"),
        ('"Foo"', "Foo"),
        ("'Foo'", "Foo"),
        ('Optional["Foo"]', "Foo"),
        ("list[Foo]", "list"),
        ("dict[str, Foo]", "dict"),
        ("pkg.mod.Foo", "Foo"),
        ("List<Order>?", "List"),
    ],
)
def test_simple_type_name_handles_python_annotations(raw: str, expected: str) -> None:
    assert adapter.simple_type_name(raw) == expected


def test_creation_by_call_resolves_to_constructor(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        "class Foo:\n"
        "    def __init__(self, a: int):\n"
        "        self.a = a\n"
        "    def go(self) -> 'Foo':\n"
        "        return self\n"
        "\n"
        "def helper() -> None:\n"
        "    pass\n"
        "\n"
        "def run(shadow: int):\n"
        "    Foo(1)\n"
        "    Foo(1).go()\n"
        "    helper()\n"
        "    shadow()\n"
        "    x: Optional[Foo] = None\n"
        "    x.go()\n",
        encoding="utf-8",
    )
    index = build_index(tmp_path, ["pkg/m.py"], adapter)
    run = next(m for m in index.callables.values() if m.name == "run")
    calls = extract_calls(index, run).calls
    foo = calls[0]
    assert foo.name == "Foo"
    assert foo.is_creation
    assert isinstance(foo.resolution, Resolved)
    assert foo.resolution.type == TypeKey("pkg.m.Foo")
    assert foo.resolution.member is not None
    assert foo.resolution.member.kind == "constructor"
    by_name = {c.name: c for c in calls if c.name != "Foo"}
    assert not by_name["helper"].is_creation
    assert not by_name["shadow"].is_creation
    chained = by_name["go"]
    assert isinstance(chained.resolution, Resolved)
    assert chained.resolution.type == TypeKey("pkg.m.Foo")


@pytest.mark.parametrize(
    "expression",
    [
        "[t.as_row() for t in ts]",
        "{t.as_row() for t in ts}",
        "{t.key(): t.as_row() for t in ts}",
        "(t.as_row() for t in ts)",
    ],
)
def test_calls_inside_comprehensions_are_the_outer_functions_calls(expression: str) -> None:
    sites = _sites(f"def f(ts):\n    return {expression}\n")
    names = [s.name for s in sites if isinstance(s, CallSite)]
    assert "as_row" in names


def test_call_whose_only_argument_is_a_generator_expression_is_found() -> None:
    sites = _sites("def f(ts):\n    return '\\n'.join(t.as_row() for t in ts)\n")
    assert [s.name for s in sites if isinstance(s, CallSite)] == ["as_row", "join"]
    sites = _sites("def f(ts):\n    return sum(g(t) for t in ts)\n")
    assert [s.name for s in sites if isinstance(s, CallSite)] == ["g", "sum"]


_RECEIVER_SOURCE = """
class Inner:
    def go(self) -> None: ...

class Outer:
    class Nested:
        def deep(self) -> None: ...
    held: Inner

    def hold(self) -> None:
        self.held.go()

class Base:
    def ping(self) -> None: ...

class Child(Base):
    def run(self) -> None:
        super().ping()

class Orphan(External):
    def run(self) -> None:
        super().ping()

class Lonely:
    def run(self) -> None:
        super().ping()

def walk(o: Outer, e: Ext) -> None:
    o.Nested.deep()
    o.missing.go()
    e.thing.go()
    (o).hold()
    [1].append(2)
"""


def _described(resolution: Resolution) -> str:
    match resolution:
        case Resolved():
            return f"resolved {resolution.type.qualified_name}"
        case External():
            return f"external {resolution.type}"
        case Unresolved():
            return f"unresolved {resolution.text}"


def test_receiver_expressions_resolve_to_the_type_they_name(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(_RECEIVER_SOURCE, encoding="utf-8")
    index = build_index(tmp_path, ["m.py"], adapter)
    described = {
        m.qualified_name: [
            (c.name, _described(c.resolution)) for c in extract_calls(index, m).calls
        ]
        for m in index.callables.values()
        if m.name in ("run", "walk", "hold")
    }
    assert described == {
        # フィールドの型注記、super() の基底（索引の型、索引に無い型、基底なし）
        "m.Outer.hold": [("go", "resolved m.Inner")],
        "m.Child.run": [("ping", "resolved m.Base")],
        "m.Orphan.run": [("ping", "external External")],
        "m.Lonely.run": [("ping", "unresolved super()")],
        # 入れ子の型、無いメンバ、外部の型のメンバ、括弧、リテラル
        "m.walk": [
            ("deep", "resolved m.Outer.Nested"),
            ("go", "unresolved o.missing"),
            ("go", "unresolved e.thing"),
            ("hold", "resolved m.Outer"),
            ("append", "unresolved [1]"),
        ],
    }
