from tree_sitter import Node

from jigane.languages.base import Branch, BranchArm, DiscriminatorReader, Return, Step, parse
from jigane.languages.swift import SwiftAdapter
from jigane.preconditions.required import required


def _walk(node: Node) -> list[Node]:
    return [node, *(d for c in node.children for d in _walk(c))]


def _branches(source: str) -> list[Step]:
    adapter = SwiftAdapter()
    root = parse(adapter, source.encode()).root_node
    function = next(n for n in _walk(root) if n.type == "function_declaration")
    return adapter.discriminator_branches(required(adapter.body_of(function)), b"")


def _function(body: str) -> list[Step]:
    return _branches(f"func m(x: Int, o: Int?) -> Kind {{ {body} }}")


def test_swift_adapter_is_a_discriminator_reader() -> None:
    assert isinstance(SwiftAdapter(), DiscriminatorReader)


def test_discriminator_if_chain_nests_else_if_and_reads_binding_condition() -> None:
    steps = _function(
        "if x > 1 { return .a } else if let y = o, y == .b { return Kind.b } else { throw E() }"
    )
    first = steps[0]
    assert isinstance(first, Branch)
    assert first.condition == "x > 1"
    assert first.arms[0] == BranchArm("yes", (Return("Kind.a"),))
    nested = first.arms[1].steps[0]
    assert isinstance(nested, Branch)
    assert nested.condition == "let y = o, y == .b"
    assert nested.arms[0].steps == (Return("Kind.b"),)
    assert nested.arms[1] == BranchArm("no", (Return(None),))


def test_discriminator_if_without_else_keeps_implicit_no_arm() -> None:
    steps = _function("if x > 1 { return .a }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert [a.label for a in branch.arms] == ["yes"]
    assert branch.fallthrough_label == "no"


def test_discriminator_guard_puts_else_body_in_no_arm_and_rest_in_yes_arm() -> None:
    steps = _function("guard x > 0 else { return .b }; return .a")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert branch.condition == "x > 0"
    assert branch.arms == (
        BranchArm("yes", (Return("Kind.a"),)),
        BranchArm("no", (Return("Kind.b"),)),
    )
    assert len(steps) == 1


def test_discriminator_switch_labels_and_member_values() -> None:
    steps = _function(
        "switch x { case 1: return .a\n case 2, 3: return .b\n"
        " case .foo(let z) where z > 1: return Kind.c\n default: return .d }"
    )
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert branch.condition == "switch x"
    assert [a.label for a in branch.arms] == [
        "case 1",
        "case 2, 3",
        "case .foo(let z) where z > 1",
        "default",
    ]
    assert [a.steps for a in branch.arms] == [
        (Return("Kind.a"),),
        (Return("Kind.b"),),
        (Return("Kind.c"),),
        (Return("Kind.d"),),
    ]


def test_discriminator_nested_branch_inside_switch_arm() -> None:
    steps = _function(
        "switch x { case 1: if o == nil { return .a } else { return .b }\n default: break }"
    )
    branch = steps[0]
    assert isinstance(branch, Branch)
    inner = branch.arms[0].steps[0]
    assert isinstance(inner, Branch)
    assert inner.condition == "o == nil"
    assert branch.arms[1].steps == ()


def test_discriminator_return_of_call_is_valueless_end() -> None:
    steps = _function("if x > 1 { return make(.a) }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert branch.arms[0].steps == (Return(None),)


def test_discriminator_switch_expression_returns_member_values() -> None:
    steps = _function("return switch x { case 1: .a\n default: .b }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert [a.steps for a in branch.arms] == [(Return("Kind.a"),), (Return("Kind.b"),)]


def test_discriminator_single_expression_body_reads_if_expression() -> None:
    steps = _function("if x > 1 { .a } else { .b }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert [a.steps for a in branch.arms] == [(Return("Kind.a"),), (Return("Kind.b"),)]


def test_discriminator_ignores_unreadable_statements() -> None:
    assert (
        _function(
            "helper(); for i in 0..<x { work(i) }; let k: Kind = if x > 1 { .a } else { .b }"
        )
        == []
    )


def test_discriminator_member_value_stays_unqualified_without_return_type() -> None:
    steps = _branches("func m(x: Int) { if x > 1 { return .a } }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert branch.arms[0].steps == (Return(".a"),)


def test_discriminator_optional_return_type_qualifies_member_value() -> None:
    steps = _branches("func m(x: Int) -> Kind? { if x > 1 { return .a }; return nil }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert branch.arms[0].steps == (Return("Kind.a"),)


def test_single_call_statement_is_not_read_as_a_return() -> None:
    steps = _branches("func run(x: Bool) { if x { log() } }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert all(not arm.steps for arm in branch.arms)


def test_single_call_in_a_typed_function_is_an_implicit_return() -> None:
    steps = _function("if x > 1 { make() } else { .a }")
    branch = steps[0]
    assert isinstance(branch, Branch)
    assert branch.arms[0].steps == (Return(None),)
    assert branch.arms[1].steps == (Return("Kind.a"),)
