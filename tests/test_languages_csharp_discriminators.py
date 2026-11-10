from tree_sitter import Node

from jigane.languages.base import Branch, BranchArm, DiscriminatorReader, Return, Step, parse
from jigane.languages.csharp import CSharpAdapter
from jigane.preconditions.required import required


def _walk(node: Node) -> list[Node]:
    return [node, *(d for c in node.children for d in _walk(c))]


def _members(source: str) -> list[Step]:
    adapter = CSharpAdapter()
    root = parse(adapter, source.encode()).root_node
    method = next(n for n in _walk(root) if n.type == "method_declaration")
    return adapter.discriminator_branches(required(adapter.body_of(method)), b"")


def _function(body: str) -> list[Step]:
    return _members(f"class C {{ Kind M(int x, object o) {{ {body} }} }}")


def _branch(step: Step) -> Branch:
    assert isinstance(step, Branch)
    return step


def test_csharp_adapter_is_a_discriminator_reader() -> None:
    assert isinstance(CSharpAdapter(), DiscriminatorReader)


def test_if_chain_nests_else_if_and_else() -> None:
    steps = _function(
        "if (x > 1) { return Kind.A; } else if (o is string s) return N.Kind.B;"
        " else { throw new E(); }"
    )
    first = _branch(steps[0])
    assert first.condition == "x > 1"
    assert first.arms[0] == BranchArm("yes", (Return("Kind.A"),))
    nested = _branch(first.arms[1].steps[0])
    assert nested.condition == "o is string s"
    assert nested.arms[0].steps == (Return("N.Kind.B"),)
    assert nested.arms[1] == BranchArm("no", (Return(None),))


def test_if_without_else_has_a_fallthrough_label() -> None:
    first = _branch(_function("if (x > 1) return Kind.A; return Kind.B;")[0])
    assert first.fallthrough_label == "no"
    assert len(first.arms) == 1


def test_switch_statement_binds_empty_sections_and_reads_default_and_when() -> None:
    steps = _function(
        "switch (x) { case 1: case 2 when x > 0: return Kind.A;"
        " case int y when y > 3 && y < 9: goto case 1; default: break; }"
    )
    switch = _branch(steps[0])
    assert switch.condition == "switch x"
    assert [a.label for a in switch.arms] == [
        "case 1, 2 when x > 0",
        "case int y when y > 3 && y < 9",
        "default",
    ]
    assert switch.arms[0].steps == (Return("Kind.A"),)
    assert switch.arms[1].steps == ()
    assert switch.arms[2].steps == ()


def test_switch_expression_in_return_reads_arrow_left_and_throw() -> None:
    steps = _function("return x switch { 1 or 2 => Kind.A, > 5 => f(x), _ => throw new E() };")
    switch = _branch(steps[0])
    assert switch.condition == "switch x"
    assert [a.label for a in switch.arms] == ["1 or 2", "> 5", "_"]
    values = [a.steps for a in switch.arms]
    assert values == [(Return("Kind.A"),), (Return(None),), (Return(None),)]


def test_bare_block_statement_is_read_inside_switch_arm_and_after_if() -> None:
    steps = _function(
        "switch (x) { case 1: { return Kind.A; } default: break; }"
        " if (x > 1) { return Kind.A; } { return Kind.B; }"
    )
    switch = _branch(steps[0])
    assert switch.arms[0].steps == (Return("Kind.A"),)
    assert steps[2] == Return("Kind.B")


def test_expression_bodied_member_is_an_implicit_return() -> None:
    switched = _members("class C { Kind M(int x) => x switch { 1 => Kind.A, _ => Kind.B }; }")
    assert [a.label for a in _branch(switched[0]).arms] == ["1", "_"]
    assert _members("class C { Kind M(int x) => Kind.A; }") == [Return("Kind.A")]
    assert _members("class C { Kind M(int x) => throw new E(); }") == [Return(None)]


def test_throw_statement_is_a_return_without_value() -> None:
    assert _function("throw new E();") == [Return(None)]


def test_return_value_keeps_only_member_references() -> None:
    steps = _function(
        "if (x > 0) return Kind.A.B(); if (x > 1) return Items[0]; if (x > 2) return 1;"
        " return N.Kind.A;"
    )
    values = [_branch(s).arms[0].steps for s in steps[:3]]
    assert values == [(Return(None),)] * 3
    assert steps[3] == Return("N.Kind.A")


def test_yield_return_conditional_and_assignment_switch_are_not_read() -> None:
    steps = _function(
        "yield return Kind.A; var k = x > 1 ? Kind.A : Kind.B;"
        " var r = x switch { 1 => Kind.A, _ => Kind.B };"
    )
    assert steps == []


def test_loops_try_and_local_functions_are_not_read() -> None:
    steps = _function(
        "foreach (var i in o) { if (i is null) return Kind.A; }"
        " try { return Kind.B; } finally { }"
        " using (var d = o) { return Kind.A; } lock (o) { return Kind.A; }"
        " Kind Local() { if (x > 1) return Kind.A; return Kind.B; }"
        " Func<int> f = () => { if (x > 1) return 1; return 2; };"
    )
    assert steps == []
