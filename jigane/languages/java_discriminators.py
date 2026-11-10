"""Java のメソッドの本体から、判別の流れ図に渡す分岐と return を読む。

読む文は if（else if、else）、switch 文、return、yield、throw。
throw は値なしの終わりとして読む。switch 式は、return と yield の式のときに読む。
switch 式の腕の中では、式文を値として読む。
return の値は `Kind.A` の形のフィールド参照だけを残す。
ループ、try、ラムダの本体は読まない。
"""

from tree_sitter import Node

from jigane.languages import discriminators_core as core
from jigane.languages.base import Return, Step
from jigane.languages.node_text import text_of


def discriminator_steps(body_node: Node) -> list[Step]:
    """本体の分岐と return を文書順に返す。"""
    return core.steps(_statements(body_node), _RULES)


def _statements(node: Node) -> list[Node]:
    # 波括弧の無い腕と枝は、文一つがそのまま本体になる
    return list(node.named_children) if node.type == "block" else [node]


def _return_or_yield(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    del is_value
    expression = statement.named_children[0] if statement.named_children else None
    if expression is not None and expression.type == "switch_expression":
        return [core.switch_branch(expression, _RULES, is_value=True)]
    return [Return(core.member_text(expression, _RULES))]


def _if_statement(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    del is_value
    return [core.if_branch(statement, _RULES)]


def _switch_expression(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    del is_value
    return [core.switch_branch(statement, _RULES)]


def _expression_value(statement: Node, *, is_value: bool) -> Step | None:
    # switch 式の腕の中では、式文を値として読む
    if statement.type == "expression_statement" and is_value and statement.named_children:
        return Return(core.member_text(statement.named_children[0], _RULES))
    return None


def _condition_text(statement: Node) -> str:
    condition = statement.child_by_field_name("condition")
    return text_of(condition)[1:-1].strip() if condition is not None else "?"


def _then_steps(statement: Node, *, is_value: bool) -> list[Step]:
    del is_value
    consequence = statement.child_by_field_name("consequence")
    return core.steps(_statements(consequence), _RULES) if consequence else []


def _else_steps(statement: Node, *, is_value: bool) -> list[Step] | None:
    del is_value
    alternative = statement.child_by_field_name("alternative")
    return None if alternative is None else core.steps(_statements(alternative), _RULES)


def _switch_subject(statement: Node) -> str:
    subject = next(
        (c for c in statement.named_children if c.type == "parenthesized_expression"), None
    )
    return text_of(subject)[1:-1].strip() if subject is not None else ""


def _switch_clauses(statement: Node) -> list[core.Clause]:
    block = next((c for c in statement.named_children if c.type == "switch_block"), None)
    clauses: list[core.Clause] = []
    for clause in block.named_children if block is not None else []:
        label = next((c for c in clause.named_children if c.type == "switch_label"), None)
        if label is None:
            continue
        name = text_of(label).removeprefix("case ").strip()
        if clause.type == "switch_rule":
            body = clause.named_children[-1]
            clauses.append(core.Clause(name, () if body is label else tuple(_statements(body))))
        else:
            nodes = [c for c in clause.named_children if c is not label]
            # 本体が空のラベルは次のラベルへ落ちる
            clauses.append(core.Clause(name, tuple(nodes), falls_through=not nodes))
    return clauses


_RULES = core.Rules(
    member_nodes=frozenset({"field_access"}),
    member_pattern=None,
    statement_readers={
        "return_statement": _return_or_yield,
        "yield_statement": _return_or_yield,
        "if_statement": _if_statement,
        "switch_expression": _switch_expression,
    },
    is_throw=lambda statement: statement.type == "throw_statement",
    condition_text=_condition_text,
    then_steps=_then_steps,
    else_steps=_else_steps,
    switch_subject=_switch_subject,
    switch_clauses=_switch_clauses,
    arm_name=core.joined_case_label,
    bundles_fallthrough=True,
    value_reader=_expression_value,
)
