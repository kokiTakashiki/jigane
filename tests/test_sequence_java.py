from pathlib import Path

import pytest
from review_fakes import changed_segment, region_of
from tree_sitter import Node

from jigane.judgment_map.classification import Segment
from jigane.languages.base import (
    Branch,
    BranchArm,
    CallSite,
    DeferredSite,
    Return,
    SequenceAdapter,
    Step,
    Visibility,
    parse,
)
from jigane.languages.java import JavaAdapter
from jigane.languages.registry import adapter_for
from jigane.preconditions.required import required
from jigane.sequence.diagram import build_diagrams, to_mermaid
from jigane.sequence.index import Index, Member, TypeKey, build_index
from jigane.sequence.resolve import (
    External,
    Extraction,
    Resolution,
    Resolved,
    Unresolved,
    extract_calls,
)
from jigane.sequence.roots import changed_members, select_roots

_SOURCE = """
import java.util.List;

public class Rules extends Base {
    private final Store store;
    private Customer owner;

    Rules(Store store) { this.store = store; }

    void field() { store.save(); }
    void argument(Order order) { order.confirm(); }
    void annotated() { Order o = store.load(); o.confirm(); }
    void inferred() { var o = new Order(); o.confirm(); }
    void inferredFromCall() { var o = store.load(); o.confirm(); }
    void self() { this.helper(); helper(); super.inherited(); }
    void member() { this.owner.notify(); }
    void statics() { Util.help(); }
    void external() { Objects.requireNonNull(store); }
    void unresolved() { thing.go(); }
    void loop(List<Order> orders) { for (Order each : orders) { each.confirm(); } }
    void catching() { try { store.save(); } catch (IllegalStateException e) { e.getMessage(); } }
    void pattern(Object o) { if (o instanceof Customer c) { c.notify(); } }
    void creation() { new Order(1); new Store.Inner(); }
    void lambdas() { run(() -> store.save()); run(x -> store.load()); }
    void references() { run(Util::help); run(this::helper); run(store::save); }
    void anonymous() { run(new Runnable() { public void run() { store.save(); } }); }
    void chained() { store.load().confirm(); }
    void overloaded() { store.save(1); }
    void helper() { }
    void run(Object action) { }
    int twoArgs(int a, String b) { return 0; }
}

class Base { void inherited() { } }
class Order { Order() { } Order(int n) { } void confirm() { } }
class Customer { void notify() { } }
class Util { static void help() { } }
interface Store {
    Order load();
    void save();
    void save(int n);
    class Inner { }
}
"""


@pytest.fixture(scope="module")
def index(tmp_path_factory: pytest.TempPathFactory) -> Index:
    root = tmp_path_factory.mktemp("java")
    (root / "Rules.java").write_text(_SOURCE, encoding="utf-8")
    return build_index(root, ["Rules.java"], JavaAdapter())


def _method(index: Index, owner: str, name: str) -> Member:
    return index.types[TypeKey(owner)].members[name][0]


def _extract(index: Index, name: str) -> Extraction:
    return extract_calls(index, _method(index, "Rules", name))


def _resolved(resolution: Resolution) -> Resolved:
    assert isinstance(resolution, Resolved)
    return resolution


def _only(index: Index, name: str) -> Resolution:
    calls = _extract(index, name).calls
    assert len(calls) == 1
    return calls[0].resolution


def test_java_adapter_is_a_sequence_adapter_in_the_registry() -> None:
    assert isinstance(adapter_for("a/B.java"), SequenceAdapter)


def test_constructor_keeps_the_type_name_form(index: Index) -> None:
    assert index.types[TypeKey("Rules")].members["Rules"][0].qualified_name == "Rules.Rules"


def test_index_holds_types_members_and_bases(index: Index) -> None:
    assert index.types[TypeKey("Rules")].bases == ("Base",)
    assert index.types[TypeKey("Store")].kind == "interface"
    assert index.types[TypeKey("Store.Inner")].kind == "class"
    assert index.types[TypeKey("Rules")].members["store"][0].type_name == "Store"
    assert index.types[TypeKey("Rules")].members["Rules"][0].kind == "constructor"
    assert _method(index, "Store", "load").type_name == "Order"
    assert len(index.types[TypeKey("Store")].members["save"]) == 2


def test_field_argument_and_annotated_local(index: Index) -> None:
    assert _resolved(_only(index, "field")).type == TypeKey("Store")
    assert _resolved(_only(index, "argument")).type == TypeKey("Order")
    calls = _extract(index, "annotated").calls
    assert [_resolved(c.resolution).type for c in calls] == [
        TypeKey("Store"),
        TypeKey("Order"),
    ]


def test_var_takes_the_type_of_a_creation_or_a_call(index: Index) -> None:
    created = _extract(index, "inferred").calls
    assert [_resolved(c.resolution).type for c in created] == [
        TypeKey("Order"),
        TypeKey("Order"),
    ]
    from_call = _extract(index, "inferredFromCall").calls
    assert [_resolved(c.resolution).type for c in from_call] == [
        TypeKey("Store"),
        TypeKey("Order"),
    ]


def test_this_super_and_receiverless_calls(index: Index) -> None:
    calls = _extract(index, "self").calls
    members = [_resolved(c.resolution).member for c in calls]
    assert [m.qualified_name if m else None for m in members] == [
        "Rules.helper",
        "Rules.helper",
        "Base.inherited",
    ]
    assert _resolved(calls[2].resolution).type == TypeKey("Base")


def test_this_field_chain(index: Index) -> None:
    assert _resolved(_only(index, "member")).type == TypeKey("Customer")


def test_static_call_on_a_type_name(index: Index) -> None:
    resolution = _resolved(_only(index, "statics"))
    assert resolution.type == TypeKey("Util")
    assert resolution.member is not None
    assert resolution.member.qualified_name == "Util.help"


def test_unknown_capitalised_type_is_external_and_unknown_variable_is_unresolved(
    index: Index,
) -> None:
    assert _only(index, "external") == External("Objects")
    assert _only(index, "unresolved") == Unresolved("thing")


def test_enhanced_for_catch_and_instanceof_variables(index: Index) -> None:
    assert _resolved(_only(index, "loop")).type == TypeKey("Order")
    catching = _extract(index, "catching").calls
    assert _resolved(catching[0].resolution).type == TypeKey("Store")
    assert catching[1].resolution == External("IllegalStateException")
    assert _resolved(_only(index, "pattern")).type == TypeKey("Customer")


def test_creation_resolves_to_the_constructor_by_argument_count(index: Index) -> None:
    calls = _extract(index, "creation").calls
    assert all(c.is_creation for c in calls)
    order = _resolved(calls[0].resolution)
    assert order.type == TypeKey("Order")
    assert order.member is not None
    assert [p.name for p in order.member.parameters] == ["n"]
    assert _resolved(calls[1].resolution).type == TypeKey("Store.Inner")


def test_call_result_as_receiver(index: Index) -> None:
    calls = _extract(index, "chained").calls
    assert [c.name for c in calls] == ["load", "confirm"]
    assert _resolved(calls[1].resolution).type == TypeKey("Order")


def test_overloads_are_bound_by_name_and_narrowed_by_argument_count(index: Index) -> None:
    resolution = _resolved(_only(index, "overloaded"))
    assert resolution.candidates == 1
    assert resolution.member is not None
    assert [p.name for p in resolution.member.parameters] == ["n"]
    assert _resolved(_only(index, "field")).candidates == 1


def test_lambda_calls_are_deferred_with_their_targets(index: Index) -> None:
    extraction = _extract(index, "lambdas")
    assert [c.name for c in extraction.calls] == ["run", "run"]
    lambdas = [d for d in extraction.deferred if d.kind == "lambda"]
    assert [[_resolved(t).type for t in d.targets] for d in lambdas] == [
        [TypeKey("Store")],
        [TypeKey("Store")],
    ]


def test_method_references_resolve_on_a_type_this_and_a_field(index: Index) -> None:
    references = [d for d in _extract(index, "references").deferred if d.kind == "method_group"]
    targets = [_resolved(d.targets[0]) for d in references]
    assert [(t.type, t.member.name if t.member else None) for t in targets] == [
        (TypeKey("Util"), "help"),
        (TypeKey("Rules"), "helper"),
        (TypeKey("Store"), "save"),
    ]


def test_anonymous_class_body_calls_are_deferred(index: Index) -> None:
    extraction = _extract(index, "anonymous")
    assert [c.name for c in extraction.calls] == ["Runnable", "run"]
    assert [len(d.targets) for d in extraction.deferred] == [1]


def _sites(source: str, name: str) -> list[CallSite | DeferredSite]:
    adapter = JavaAdapter()
    data = source.encode()
    root = parse(adapter, data).root_node
    declaration = next(d for d in adapter.type_members(root, data, "C.java") if d.name == name)
    body = adapter.body_of(declaration.node)
    assert body is not None
    return adapter.call_sites(body, data)


def test_call_sites_follow_evaluation_order_and_flag_creations() -> None:
    sites = _sites(
        "class C { void m() { outer(inner()); var w = new Widget(make()); a.b().c(); } }", "m"
    )
    calls = [s for s in sites if isinstance(s, CallSite)]
    assert [c.name for c in calls] == ["inner", "outer", "make", "Widget", "b", "c"]
    assert [c.is_creation for c in calls] == [False, False, False, True, False, False]
    assert calls[0].receiver is None
    assert calls[5].receiver is not None
    assert calls[5].receiver.type == "method_invocation"


def _branches(body: str) -> list[Step]:
    adapter = JavaAdapter()
    root = parse(adapter, f"class C {{ Kind m(Object o, int x) {{ {body} }} }}".encode()).root_node
    method = next(n for n in _walk(root) if n.type == "method_declaration")
    steps = adapter.discriminator_branches(required(adapter.body_of(method)), b"")
    return required(steps)


def _walk(node: Node) -> list[Node]:
    return [node, *(d for c in node.children for d in _walk(c))]


def test_discriminator_if_chain_nests_else_if_and_reads_instanceof_pattern() -> None:
    steps = _branches(
        "if (x > 1) { return Kind.A; } "
        "else if (o instanceof Foo f && f.ok()) return Kind.B; "
        "else { throw new X(); }"
    )
    first = steps[0]
    assert isinstance(first, Branch)
    assert first.condition == "x > 1"
    assert first.arms[0] == BranchArm("yes", (Return("Kind.A"),))
    nested = first.arms[1].steps[0]
    assert isinstance(nested, Branch)
    assert nested.condition == "o instanceof Foo f && f.ok()"
    assert nested.arms[0].steps == (Return("Kind.B"),)
    assert nested.arms[1] == BranchArm("no", (Return(None),))


def test_discriminator_switch_statement_colon_form_merges_fallthrough_labels() -> None:
    steps = _branches(
        "switch (x) { case 1: return Kind.A; case 2: case 3: return Kind.B; "
        "default: return Kind.C; }"
    )
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert branch.condition == "switch x"
    assert [a.label for a in branch.arms] == ["case 1", "case 2, 3", "default"]
    assert branch.arms[1].steps == (Return("Kind.B"),)


def test_discriminator_switch_statement_arrow_form() -> None:
    steps = _branches("switch (x) { case 1 -> { return Kind.A; } default -> throw new X(); }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert [a.steps for a in branch.arms] == [(Return("Kind.A"),), (Return(None),)]


def test_discriminator_switch_expression_with_patterns() -> None:
    steps = _branches(
        "return switch (o) { case Foo f when f.ok() -> Kind.A; case Bar b -> Kind.B; "
        "default -> compute(); };"
    )
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert branch.condition == "switch o"
    assert [a.label for a in branch.arms] == ["case Foo f when f.ok()", "case Bar b", "default"]
    assert [a.steps for a in branch.arms] == [
        (Return("Kind.A"),),
        (Return("Kind.B"),),
        (Return(None),),
    ]


def test_discriminator_body_without_branch_yields_no_steps() -> None:
    assert _branches("helper(); for (int i = 0; i < x; i++) { work(); }") == []


def _segments(index: Index, names: list[str]) -> list[Segment]:
    segments: list[Segment] = []
    for member in index.callables.values():
        declaration = member.declaration
        if declaration is not None and member.qualified_name in names:
            line = declaration.span.start_line
            segments.append(changed_segment(member.path, line, line, member.qualified_name))
    return segments


def test_mermaid_for_a_change_across_two_classes(tmp_path: Path) -> None:
    (tmp_path / "Service.java").write_text(
        "class Service {\n"
        "    private final Repo repo;\n"
        "    Service(Repo repo) { this.repo = repo; }\n"
        "    void run(Item item) {\n"
        "        repo.save(item);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "Repo.java").write_text(
        "class Repo {\n    void save(Item item) {\n        item.touch();\n    }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "Item.java").write_text(
        "class Item {\n    void touch() { }\n}\n", encoding="utf-8"
    )
    paths = ["Item.java", "Repo.java", "Service.java"]
    index = build_index(tmp_path, paths, JavaAdapter())
    segments = _segments(index, ["Service.run", "Repo.save"])
    changed = changed_members(index, segments, JavaAdapter(), include_tests=False)
    selection = select_roots(index, changed, JavaAdapter())
    regions = [region_of({"id": "J1", "path": "Repo.java", "qualified_name": "Repo.save"})]
    model = build_diagrams(index, selection, regions)
    assert [d["root"] for d in model["diagrams"]] == ["Service.run"]
    assert to_mermaid(model["diagrams"][0]) == (
        "sequenceDiagram\n"
        "    participant P0 as Service\n"
        "    participant P1 as Repo\n"
        "    P0->>P1: save() [J1]\n"
        "    Note over P0: 変更に関わらない呼び出し 1 件\n"
    )


_ANONYMOUS_SOURCE = """\
class Adapters {
    static final Adapter URL = new Adapter<Url>() {
        public Url read(Reader in) {
            return in.next();
        }
    };
    Adapter<Item> make() {
        Runnable task = () -> run();
        return new Adapter<Item>() {
            public void write(Writer out) {
                out.flush();
            }
            public Item read(Reader in) {
                return new Adapter<Item>() { Item read(Reader r) { return r.next(); } }.read(in);
            }
        };
    }
}
class Adapter<T> { T read(Reader in) { return null; } }
"""


@pytest.fixture
def anonymous_index(tmp_path: Path) -> Index:
    (tmp_path / "Adapters.java").write_text(_ANONYMOUS_SOURCE, encoding="utf-8")
    return build_index(tmp_path, ["Adapters.java"], JavaAdapter())


def _anonymous_segment(name: str, line: int) -> Segment:
    return changed_segment("Adapters.java", line, line, name)


def test_anonymous_class_methods_are_declarations_inside_the_enclosing_declaration() -> None:
    source = _ANONYMOUS_SOURCE.encode()
    declarations = JavaAdapter().declarations(parse(JavaAdapter(), source).root_node, source)
    by_name = {d.qualified_name: d for d in declarations}
    assert by_name["Adapters.URL.<anonymous1>.read"].span.start_line == 3
    assert by_name["Adapters.URL.<anonymous1>.read"].span.end_line == 5
    assert by_name["Adapters.URL"].span.start_line == 2
    assert by_name["Adapters.URL.<anonymous1>.read"].visibility is Visibility.FILE
    # 無名クラスの中の無名クラスは、外側の無名クラスのメンバの下に入れ子になる
    assert "Adapters.make.<anonymous1>.read.<anonymous1>.read" in by_name
    # ラムダは宣言にせず、本体は外側のメソッドに属する
    assert not any("lambda" in name for name in by_name)


def test_anonymous_class_is_an_indexed_type_with_its_base(anonymous_index: Index) -> None:
    info = anonymous_index.types[TypeKey("Adapters.URL.<anonymous1>")]
    assert info.bases == ("Adapter",)
    member = info.members["read"][0]
    assert member.qualified_name == "Adapters.URL.<anonymous1>.read"
    assert member.declaration is not None
    assert member.declaration.span.start_line == 3
    assert (member.path, member.line) in anonymous_index.callables


def test_changed_row_in_an_anonymous_method_makes_that_method_a_root(
    anonymous_index: Index,
) -> None:
    segments = [_anonymous_segment("Adapters.URL.<anonymous1>.read", 4)]
    changed = changed_members(anonymous_index, segments, JavaAdapter(), include_tests=False)
    selection = select_roots(anonymous_index, changed, JavaAdapter())
    assert [r.member.qualified_name for r in selection.roots] == ["Adapters.URL.<anonymous1>.read"]


def test_two_anonymous_methods_in_one_method_are_separate_roots(anonymous_index: Index) -> None:
    segments = [
        _anonymous_segment("Adapters.make.<anonymous1>.write", 11),
        _anonymous_segment("Adapters.make.<anonymous1>.read", 13),
    ]
    changed = changed_members(anonymous_index, segments, JavaAdapter(), include_tests=False)
    selection = select_roots(anonymous_index, changed, JavaAdapter())
    assert sorted(r.member.name for r in selection.roots) == ["read", "write"]


def test_anonymous_class_participant_has_no_angle_brackets(anonymous_index: Index) -> None:
    segments = [_anonymous_segment("Adapters.URL.<anonymous1>.read", 4)]
    changed = changed_members(anonymous_index, segments, JavaAdapter(), include_tests=False)
    selection = select_roots(anonymous_index, changed, JavaAdapter())
    model = build_diagrams(anonymous_index, selection, [])
    participants = model["diagrams"][0]["participants"]
    # 外部の型 Reader だけを呼ぶ入口なので、外部の参加者が続く
    assert [p["display"] for p in participants] == ["URL の無名クラス 1", "Reader"]
    assert "<" not in to_mermaid(model["diagrams"][0])


_TWO_ANONYMOUS_SOURCE = """\
class Pair {
    static final Object BOTH = combine(
        new Adapter<A>() { A read(Reader r) { return r.a(); } },
        new Adapter<B>() { B read(Reader r) { return r.b(); } });
}
"""


def test_two_anonymous_classes_in_one_declaration_get_distinct_names(tmp_path: Path) -> None:
    (tmp_path / "Pair.java").write_text(_TWO_ANONYMOUS_SOURCE, encoding="utf-8")
    adapter = JavaAdapter()
    source = _TWO_ANONYMOUS_SOURCE.encode()
    root = parse(adapter, source).root_node
    declared = {d.qualified_name for d in adapter.declarations(root, source)}
    assert {"Pair.BOTH.<anonymous1>.read", "Pair.BOTH.<anonymous2>.read"} <= declared
    index = build_index(tmp_path, ["Pair.java"], adapter)
    # 宣言の収集と型の索引は同じ名前を引く
    assert index.types[TypeKey("Pair.BOTH.<anonymous1>")].members["read"][0].line == 3
    assert index.types[TypeKey("Pair.BOTH.<anonymous2>")].members["read"][0].line == 4


def test_anonymous_class_display_comes_from_the_adapter() -> None:
    adapter = JavaAdapter()
    assert adapter.anonymous_class_display("Adapters.URL.<anonymous1>") == "URL の無名クラス 1"
    assert adapter.anonymous_class_display("Pair.BOTH.<anonymous2>") == "BOTH の無名クラス 2"
    assert adapter.anonymous_class_display("Adapters.URL") is None


_OUTER_CALL_SOURCE = """\
class Outer {
    static void check() { }
    Runnable make() {
        return new Runnable() {
            public void run() { check(); own(); }
            void own() { }
        };
    }
}
"""


def test_unqualified_call_in_an_anonymous_class_resolves_to_the_enclosing_type(
    tmp_path: Path,
) -> None:
    (tmp_path / "Outer.java").write_text(_OUTER_CALL_SOURCE, encoding="utf-8")
    index = build_index(tmp_path, ["Outer.java"], JavaAdapter())
    member = index.types[TypeKey("Outer.make.<anonymous1>")].members["run"][0]
    resolutions = {c.name: c.resolution for c in extract_calls(index, member, {}).calls}
    outer = resolutions["check"]
    assert isinstance(outer, Resolved)
    assert outer.type == TypeKey("Outer")
    assert outer.member is not None
    own = resolutions["own"]
    assert isinstance(own, Resolved)
    assert own.type == TypeKey("Outer.make.<anonymous1>")
