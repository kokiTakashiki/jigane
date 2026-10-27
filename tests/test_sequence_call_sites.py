from jigane.languages.base import (
    Call,
    CallSite,
    Creation,
    DeferredSite,
    Member,
    Name,
    Paren,
    Self,
    Super,
    Unknown,
    parse,
)
from jigane.languages.csharp import CSharpAdapter

_SOURCE = b"""
class C {
    void M() {
        var a = Outer(Inner());
        var b = new Widget(Make());
        var c = nameof(a);
        list.Where(x => Check(x)).Select(Name);
        repo.Load().Save();
        Run(this.Handler);
        var d = new(1);
    }
    int P => Compute();
    int Field;
}
"""


def _sites(member_name: str) -> list[CallSite | DeferredSite]:
    adapter = CSharpAdapter()
    root = parse(adapter, _SOURCE).root_node
    declaration = next(
        d for d in adapter.type_members(root, _SOURCE, "C.cs") if d.name == member_name
    )
    body = adapter.body_of(declaration.node)
    assert body is not None
    return adapter.call_sites(body, _SOURCE)


def _calls(sites: list[CallSite | DeferredSite]) -> list[CallSite]:
    return [s for s in sites if isinstance(s, CallSite)]


def test_evaluation_order_is_inner_first_and_nameof_is_skipped() -> None:
    names = [c.name for c in _calls(_sites("M"))]
    assert names[:2] == ["Inner", "Outer"]
    assert "nameof" not in names


def test_creation_is_flagged_and_implicit_creation_is_named_new() -> None:
    creations = {c.name: c for c in _calls(_sites("M")) if c.is_creation}
    assert set(creations) == {"Widget", "new"}
    assert creations["Widget"].receiver is None
    assert isinstance(creations["Widget"].arguments, tuple)


def test_chained_calls_report_receiver_and_follow_evaluation_order() -> None:
    calls = _calls(_sites("M"))
    names = [c.name for c in calls]
    assert names.index("Load") < names.index("Save")
    save = next(c for c in calls if c.name == "Save")
    assert save.receiver is not None
    assert save.receiver.type == "invocation_expression"


def test_lambda_calls_are_deferred_not_inline() -> None:
    sites = _sites("M")
    assert "Check" not in [c.name for c in _calls(sites)]
    lambdas = [s for s in sites if isinstance(s, DeferredSite) and s.kind == "lambda"]
    assert [[c.name for c in site.calls] for site in lambdas] == [["Check"]]


def test_method_groups_are_deferred() -> None:
    groups = [s for s in _sites("M") if isinstance(s, DeferredSite) and s.kind == "method_group"]
    # 変数との区別は解決の側で行うため、nameof の引数のような裸の名前も候補に入る
    assert ("Name", True) in [(g.name, g.receiver is None) for g in groups]
    assert ("Handler", False) in [(g.name, g.receiver is None) for g in groups]


def test_expression_body_is_supported_and_field_has_no_body() -> None:
    assert [c.name for c in _calls(_sites("P"))] == ["Compute"]
    adapter = CSharpAdapter()
    root = parse(adapter, _SOURCE).root_node
    field = next(d for d in adapter.type_members(root, _SOURCE, "C.cs") if d.name == "Field")
    assert adapter.body_of(field.node) is None


def test_receiver_kinds_and_named_arguments() -> None:
    source = (
        b"class C { void M() { (a).F(x: 1, 2); this.G(); base.H(); b?.I(); new T(); a.b; 1; } }"
    )
    adapter = CSharpAdapter()
    root = parse(adapter, source).root_node
    kinds = {}
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type in ("this", "base"):
            kinds[node.type] = adapter.receiver_of(node)
    assert kinds == {"this": Self(), "base": Super()}
    member = next(d for d in adapter.type_members(root, source, "C.cs") if d.name == "M")
    body = adapter.body_of(member.node)
    assert body is not None
    sites = _calls(adapter.call_sites(body, source))
    first = sites[0]
    assert isinstance(adapter.receiver_of(first.node), Call)
    assert [a.name for a in first.arguments] == ["x", None]
    assert first.receiver is not None
    assert isinstance(adapter.receiver_of(first.receiver), Paren)
    creation = next(s for s in sites if s.is_creation)
    assert isinstance(adapter.receiver_of(creation.node), Creation)
    assert not isinstance(adapter.receiver_of(root), (Member, Name))
    assert adapter.receiver_of(root) == Unknown()


def test_generic_name_receiver_is_named_without_its_type_arguments() -> None:
    source = b"class C { void M() { List<int>.Foo(); } }"
    adapter = CSharpAdapter()
    root = parse(adapter, source).root_node
    generics = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type == "generic_name":
            generics.append(adapter.receiver_of(node))
    assert generics == [Name("List")]


def test_locals_of_reports_variables_functions_and_lambda_parameters() -> None:
    source = (
        b"class C { void M() { int a = 1; var b = new T(); void L() {} "
        b"foreach (var i in xs) {} f((p) => p); } }"
    )
    adapter = CSharpAdapter()
    root = parse(adapter, source).root_node
    member = next(d for d in adapter.type_members(root, source, "C.cs") if d.name == "M")
    body = adapter.body_of(member.node)
    assert body is not None
    found = adapter.locals_of(body)
    assert [(v.name, v.type_annotation) for v in found.variables] == [
        ("a", "int"),
        ("b", None),
        ("i", None),
    ]
    assert found.variables[1].initializer is not None
    assert found.functions == frozenset({"L"})
    assert found.lambda_parameters == frozenset({"p"})
