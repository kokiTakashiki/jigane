from jigane.languages.base import (
    Call,
    CallSite,
    Creation,
    DeferredSite,
    Member,
    Name,
    Paren,
    ReceiverKind,
    Self,
    Super,
    parse,
)
from jigane.languages.swift import SwiftAdapter
from jigane.preconditions.required import required

adapter = SwiftAdapter()

SOURCE = b"""
class A {
    func f(a: A?, list: [Int]) {
        foo(label: 1, 2)
        self.bar(x: 1)
        B.baz()
        let t = T()
        T.init(a: 1)
        a?.b()
        a!.c(y: 2)
        a.d.e()
        super.setup()
        list.forEach(handle)
        run(cb: self.handler)
        let s = #selector(go)
        let u = #selector(Foo.go(_:))
        list.map { $0.go() }
        retry(3) { r in r.again() } failure: { e in e.log() }
    }
}
"""


def _sites() -> list[CallSite | DeferredSite]:
    root = parse(adapter, SOURCE).root_node
    declarations = adapter.type_members(root, SOURCE, "A.swift")
    node = next(d.node for d in declarations if d.name == "f" and hasattr(d, "owner"))
    body = adapter.body_of(node)
    assert body is not None
    return adapter.call_sites(body, SOURCE)


def _calls() -> list[CallSite]:
    return [s for s in _sites() if isinstance(s, CallSite)]


def _call(name: str) -> CallSite:
    return next(c for c in _calls() if c.name == name)


def _kind(site: CallSite) -> ReceiverKind:
    return adapter.receiver_of(required(site.receiver))


def test_labels_are_carried_by_arguments_and_name_is_the_base_name() -> None:
    foo = _call("foo")
    assert [a.name for a in foo.arguments] == ["label", None]
    assert foo.receiver is None


def test_trailing_closures_count_as_arguments_with_their_labels() -> None:
    retry = _call("retry")
    assert [a.name for a in retry.arguments] == [None, None, "failure"]


def test_implicit_self_and_explicit_self() -> None:
    assert _call("foo").receiver is None
    assert isinstance(_kind(_call("bar")), Self)
    assert isinstance(_kind(_call("setup")), Super)


def test_type_name_variable_and_member_receivers() -> None:
    assert _kind(_call("baz")) == Name("B")
    member = _kind(_call("e"))
    assert isinstance(member, Member)
    assert member.name == "d"


def test_creation_by_call_and_by_init() -> None:
    calls = _calls()
    constructed = next(c for c in calls if c.name == "T" and not c.is_creation)
    assert constructed.receiver is None
    assert isinstance(adapter.receiver_of(constructed.node), Call)
    init = next(c for c in calls if c.is_creation)
    assert init.name == "T"
    assert init.receiver is None
    kind = adapter.receiver_of(init.node)
    assert isinstance(kind, Creation)
    assert kind.type_text == "T"


def test_optional_and_forced_chaining() -> None:
    assert _kind(_call("b")) == Name("a")
    forced = _kind(_call("c"))
    assert isinstance(forced, Paren)
    assert adapter.receiver_of(forced.inner) == Name("a")


def test_argument_closure_calls_are_calls_of_the_declaration() -> None:
    assert [s for s in _sites() if isinstance(s, DeferredSite) and s.kind == "lambda"] == []
    assert sorted(c.name for c in _calls() if c.name in ("again", "go", "log")) == [
        "again",
        "go",
        "log",
    ]


def test_function_references_and_selectors_are_deferred() -> None:
    groups = [s for s in _sites() if isinstance(s, DeferredSite) and s.kind == "method_group"]
    assert [(g.name, g.receiver is not None) for g in groups] == [
        ("handle", False),
        ("handler", True),
        ("go", False),
        ("go", True),
    ]


def test_selector_contents_are_not_calls() -> None:
    # #selector(Foo.go(_:)) の go は呼び出しではない。list.map { $0.go() } の go だけが呼び出し
    assert len([c for c in _calls() if c.name == "go"]) == 1


SWIFTUI_SOURCE = b"""
struct TimerView: View {
    var body: some View {
        VStack {
            Button("Start") { presenter.start() }
            Button(action: { presenter.stop() }) { Text("Stop") }
        }
        .onAppear { presenter.setViewVisible(true) }
    }
}
"""


def _swiftui_names() -> list[str]:
    root = parse(adapter, SWIFTUI_SOURCE).root_node
    declarations = adapter.type_members(root, SWIFTUI_SOURCE, "TimerView.swift")
    node = next(d.node for d in declarations if d.name == "body" and hasattr(d, "owner"))
    body = adapter.body_of(node)
    assert body is not None
    sites = adapter.call_sites(body, SWIFTUI_SOURCE)
    assert all(isinstance(s, CallSite) for s in sites)
    return [s.name for s in sites if isinstance(s, CallSite)]


def test_swiftui_closures_in_a_computed_property_are_calls_of_the_body() -> None:
    names = _swiftui_names()
    for expected in ("start", "stop", "setViewVisible"):
        assert expected in names


def test_stored_closure_stays_deferred() -> None:
    source = b"class A { func f() { let g = { h() } } }"
    root = parse(adapter, source).root_node
    declarations = adapter.type_members(root, source, "A.swift")
    node = next(d.node for d in declarations if d.name == "f" and hasattr(d, "owner"))
    sites = adapter.call_sites(required(adapter.body_of(node)), source)
    assert [s.kind for s in sites if isinstance(s, DeferredSite)] == ["lambda"]
