import ast
import re
from pathlib import Path

import pytest

from jigane.languages.csharp import CSharpAdapter
from jigane.sequence import index as index_module
from jigane.sequence import resolve as resolve_module
from jigane.sequence.index import Index, Member, TypeKey, build_index
from jigane.sequence.resolve import (
    External,
    Extraction,
    Resolution,
    Resolved,
    Unresolved,
    extract_calls,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sequence" / "csharp"


@pytest.fixture(scope="module")
def index() -> Index:
    return build_index(FIXTURES, ["Rules.cs"], CSharpAdapter())


def _method(index: Index, owner: str, name: str) -> Member:
    return index.types[TypeKey(owner)].members[name][0]


def _extract(index: Index, name: str, owner: str = "Rules") -> Extraction:
    return extract_calls(index, _method(index, owner, name))


def _resolved(resolution: Resolution) -> Resolved:
    assert isinstance(resolution, Resolved)
    return resolution


def _only(index: Index, name: str) -> Resolution:
    calls = _extract(index, name).calls
    assert len(calls) == 1
    return calls[0].resolution


def test_index_holds_types_members_and_bases(index: Index) -> None:
    store = index.types[TypeKey("Store")]
    assert store.bases == ("IStore",)
    assert index.types[TypeKey("IStore")].kind == "interface"
    assert index.by_name["Order"][0].qualified_name == "Order"
    owner = index.types[TypeKey("Order")].members["Owner"][0]
    assert (owner.kind, owner.type_name) == ("property", "Customer")
    assert _method(index, "IStore", "Load").type_name == "Order"
    assert index.types[TypeKey("Rules")].members["store"][0].type_name == "IStore"
    assert index.callables[("Rules.cs", _method(index, "Rules", "Rule3").line)].name == "Rule3"


def test_member_has_adapter_declaration(index: Index) -> None:
    declaration = _method(index, "Rules", "Rule3").declaration
    assert declaration is not None
    assert declaration.qualified_name == "Rules.Rule3"


def test_rule1_receiverless_this_base(index: Index) -> None:
    calls = _extract(index, "Rule1").calls
    assert [c.name for c in calls] == ["Helper", "Helper", "Inherited", "Local"]
    helper, this_helper, base_inherited, local = (_resolved(c.resolution) for c in calls)
    assert helper.type == TypeKey("Rules")
    assert helper.member is not None
    assert helper.member.qualified_name == "Base.Helper"
    assert this_helper.member is not None
    assert this_helper.member.qualified_name == "Base.Helper"
    assert base_inherited.type == TypeKey("Base")
    assert local.member is None


def test_rule2_local_variables(index: Index) -> None:
    resolutions = [_resolved(c.resolution) for c in _extract(index, "Rule2").calls]
    confirms = [r for r in resolutions if r.member is not None and r.member.name == "Confirm"]
    # 型注記付き、var = new、var = 戻り値の型注記、foreach
    assert len(confirms) == 5 - 1
    notify = resolutions[-1]
    # パターン変数 pattern は Customer
    assert notify.type == TypeKey("Customer")
    assert notify.member is not None
    assert notify.member.name == "Notify"


def test_rule3_parameter(index: Index) -> None:
    assert _resolved(_only(index, "Rule3")).type == TypeKey("Order")


def test_rule4_field_and_property_like_member(index: Index) -> None:
    assert _resolved(_only(index, "Rule4")).type == TypeKey("IStore")
    assert _resolved(_only(index, "Rule4Property")).type == TypeKey("Customer")


def test_rule4_primary_constructor_parameter(tmp_path: Path) -> None:
    source = "class P(Svc svc) { void M() { svc.Go(); } } class Svc { public void Go() { } }"
    (tmp_path / "P.cs").write_text(source, encoding="utf-8")
    index = build_index(tmp_path, ["P.cs"], CSharpAdapter())
    resolution = (
        extract_calls(index, index.types[TypeKey("P")].members["M"][0]).calls[0].resolution
    )
    assert _resolved(resolution).type == TypeKey("Svc")


def test_constructor_keeps_the_type_name_form(tmp_path: Path) -> None:
    (tmp_path / "P.cs").write_text("class P { public P() { } }", encoding="utf-8")
    index = build_index(tmp_path, ["P.cs"], CSharpAdapter())
    assert index.types[TypeKey("P")].members["P"][0].qualified_name == "P.P"


def test_rule5_static_type_name(index: Index) -> None:
    resolution = _resolved(_only(index, "Rule5"))
    assert resolution.type == TypeKey("Util")
    assert resolution.member is not None


def test_rule6_member_chain(index: Index) -> None:
    resolution = _resolved(_only(index, "Rule6"))
    assert (resolution.type, resolution.member.name if resolution.member else None) == (
        TypeKey("Customer"),
        "Notify",
    )


def test_rule7_call_result_as_receiver(index: Index) -> None:
    calls = _extract(index, "Rule7").calls
    assert [c.name for c in calls] == ["Load", "Confirm"]
    assert _resolved(calls[1].resolution).type == TypeKey("Order")
    static = _extract(index, "Rule7Static").calls
    assert _resolved(static[1].resolution).type == TypeKey("Order")


def test_rule8_capitalised_unknown_identifier_is_external(index: Index) -> None:
    assert _only(index, "Rule8") == External("ArgumentNullException")


def test_unresolved_keeps_receiver_text(index: Index) -> None:
    resolutions = [c.resolution for c in _extract(index, "Unresolved").calls]
    assert resolutions == [Unresolved("thing"), Unresolved("mystery")]


def test_rule9_concretises_interface_parameter_one_level(index: Index) -> None:
    run = _extract(index, "Rule9").calls
    assert [c.name for c in run] == ["Store", "Run"]
    outer = run[1]
    assert outer.bindings == (("target", TypeKey("Store")),)
    target = _method(index, "Rules", "Run")
    plain = extract_calls(index, target).calls[0].resolution
    assert _resolved(plain).type == TypeKey("IStore")
    concrete = extract_calls(index, target, dict(outer.bindings)).calls[0].resolution
    assert _resolved(concrete).type == TypeKey("Store")
    concrete_member = _resolved(concrete).member
    assert concrete_member is not None
    assert concrete_member.qualified_name == "Store.Save"


def test_rule9_not_applied_to_non_new_arguments(index: Index) -> None:
    run = _extract(index, "Rule9Plain").calls[0]
    assert run.bindings == ()


def test_overloads_choose_by_argument_count_then_first_declared(index: Index) -> None:
    pick1, pick2, same = (_resolved(c.resolution) for c in _extract(index, "Overloads").calls)
    assert pick1.member is not None
    assert (len(pick1.member.parameters), pick1.candidates) == (1, 1)
    assert pick2.member is not None
    assert (len(pick2.member.parameters), pick2.candidates) == (2, 1)
    assert same.member is not None
    assert (same.member.parameters[0].type_name, same.candidates) == ("int", 2)


def test_evaluation_order_is_inner_first(index: Index) -> None:
    assert [c.name for c in _extract(index, "Order1").calls] == ["Inner", "Outer"]


def test_lambda_calls_are_not_arrows_but_deferred(index: Index) -> None:
    extraction = _extract(index, "Lambdas")
    assert [c.name for c in extraction.calls] == [
        "Subscribe",
        "Subscribe",
        "new",
        "Subscribe",
        "Wrapper",
    ]
    lambdas = [d for d in extraction.deferred if d.kind == "lambda"]
    assert len(lambdas) == 2
    first = _resolved(lambdas[0].targets[0])
    assert first.member is not None
    assert first.member.qualified_name == "Order.Confirm"
    assert lambdas[0].line < lambdas[1].line


def test_method_group_references_are_deferred(index: Index) -> None:
    groups = [d for d in _extract(index, "Lambdas").deferred if d.kind == "method_group"]
    assert [g.text for g in groups] == ["Handler", "Handler", "Handler"]
    for group in groups:
        member = _resolved(group.targets[0]).member
        assert member is not None
        assert member.qualified_name == "Rules.Handler"


def test_implicit_new_is_reported_as_unresolved_call(index: Index) -> None:
    implicit = [
        c for c in _extract(index, "Lambdas").calls if c.resolution == Unresolved("new(...)")
    ]
    assert len(implicit) == 1


def test_call_location(index: Index) -> None:
    call = _extract(index, "Rule5").calls[0]
    assert (call.path, call.line) == ("Rules.cs", _method(index, "Rules", "Rule5").line)


def test_symlinks_and_escaping_paths_are_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Secret.cs").write_text("class Secret { }", encoding="utf-8")
    root = tmp_path / "tree"
    root.mkdir()
    (root / "Real.cs").write_text("class Real { }", encoding="utf-8")
    (root / "Link.cs").symlink_to(outside / "Secret.cs")
    (root / "dir").symlink_to(outside)
    index = build_index(
        root, ["Real.cs", "Link.cs", "dir/Secret.cs", "../outside/Secret.cs"], CSharpAdapter()
    )
    assert set(index.types) == {TypeKey("Real")}
    assert sorted(index.skipped) == ["../outside/Secret.cs", "Link.cs", "dir/Secret.cs"]


def test_partial_declarations_are_merged_into_one_type() -> None:
    partial = FIXTURES.parent / "partial"
    index = build_index(partial, ["A1.cs", "A2.cs"], CSharpAdapter())
    info = index.types[TypeKey("Big")]
    assert set(info.members) == {"One", "Two"}
    assert info.bases == ("Base1", "Base2")
    assert info.paths == ("A1.cs", "A2.cs")
    assert index.by_name["Big"] == [info]
    assert index.find_type("Big", "A2.cs") is info
    calls = extract_calls(index, info.members["One"][0]).calls
    assert _resolved(calls[0].resolution).member is info.members["Two"][0]


def _syntax_node_names(source: str, kinds: str, extra: frozenset[str] = frozenset()) -> list[str]:
    """文字列定数のうち、構文の節の名（`<語>_<kinds のどれか>`、extra、`preproc_` を含むもの）。"""
    pattern = re.compile(rf"[a-z_]+_({kinds})")
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (pattern.fullmatch(node.value) or node.value in extra or "preproc_" in node.value)
    ]


def _type_comparisons(source: str) -> list[int]:
    """左辺が `.type` の属性で、`==` か `in` で比べている行番号。"""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Attribute)
        and node.left.attr == "type"
        and any(isinstance(op, (ast.Eq, ast.In)) for op in node.ops)
    ]


INDEX_KINDS = "declaration|list|clause|statement"
RESOLVE_KINDS = "expression|declaration|list|clause|statement|pattern|type|name"
RESOLVE_NODE_NAMES = frozenset(
    {"identifier", "name_colon", "argument_list", "implicit_type", "variable_declarator"}
)


def test_index_has_no_syntax_node_names() -> None:
    # `jigane.languages.csharp` を import しないことは、pyproject.toml の forbidden 契約が見る
    source = Path(index_module.__file__).read_text(encoding="utf-8")
    assert _syntax_node_names(source, INDEX_KINDS) == []
    assert _type_comparisons(source) == []


def test_resolve_has_no_syntax_node_names() -> None:
    source = Path(resolve_module.__file__).read_text(encoding="utf-8")
    assert _syntax_node_names(source, RESOLVE_KINDS, RESOLVE_NODE_NAMES) == []
    assert _type_comparisons(source) == []


def test_syntax_node_names_and_type_comparisons_are_found() -> None:
    assert _syntax_node_names("x = 'class_declaration'\n", INDEX_KINDS) == ["class_declaration"]
    assert _syntax_node_names('f("preproc_if")\n', INDEX_KINDS) == ["preproc_if"]
    assert _syntax_node_names("x = 'name_colon'\n", RESOLVE_KINDS, RESOLVE_NODE_NAMES) == [
        "name_colon"
    ]
    assert _syntax_node_names("x = 'declaration list'\n", INDEX_KINDS) == []
    assert _type_comparisons("if node.type == 'x':\n    pass\n") == [1]
    assert _type_comparisons("if node.type in ('x', 'y'):\n    pass\n") == [1]
    assert _type_comparisons("if node.kind == 'x' or type == 1:\n    pass\n") == []
