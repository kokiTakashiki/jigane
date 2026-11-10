"""C# のメンバーの本体から、判別の流れ図に渡す分岐と return を読む。

読む文は if（else if、else）、switch 文、return、throw。
switch 式は、return の式のときと、式本体のメンバ（`=>`）の式のときに読む。
return の値は `Kind.A` と `N.Kind.A` の形のメンバ参照だけを残す。
ループ、try、using、lock の中と、ローカル関数とラムダの本体は読まない。
`yield return` と条件演算子（`?:`）は読まない。
"""

import re

from tree_sitter import Node

from jigane.languages import discriminators_core as core
from jigane.languages.base import Branch, BranchArm, Return, Step
from jigane.languages.node_text import text_of
from jigane.preconditions.required import required

_MEMBER_REFERENCE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")
_NON_BODY = frozenset({"when_clause", "comment", "discard"})


def _flat(text: str) -> str:
    return " ".join(text.split())


def _statements(node: Node) -> list[Node]:
    return list(node.named_children) if node.type == "block" else [node]


def discriminator_steps(body_node: Node) -> list[Step]:
    """本体の分岐と return を文書順に返す。"""
    if body_node.type == "arrow_expression_clause":
        # 式本体のメンバは、その式を暗黙の return と読む
        expression = body_node.named_children[0] if body_node.named_children else None
        return [_value_step(expression)]
    return core.steps(_statements(body_node), _RULES)


def _value_step(expression: Node | None) -> Step:
    if expression is not None and expression.type == "switch_expression":
        return _switch_expression(expression)
    return Return(core.member_text(expression, _RULES))


def _return_statement(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    del is_value
    expression = statement.named_children[0] if statement.named_children else None
    return [_value_step(expression)]


def _if_statement(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    del is_value
    return [core.if_branch(statement, _RULES)]


def _switch_statement(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    del is_value
    return [core.switch_branch(statement, _RULES)]


def _block(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    del is_value
    # 文として単独で現れる `{ ... }` は、中の文をそのまま同じ並びに読む
    return core.steps(list(statement.named_children), _RULES)


def _condition_text(statement: Node) -> str:
    condition = statement.child_by_field_name("condition")
    return _flat(text_of(condition)) if condition is not None else "?"


def _then_steps(statement: Node, *, is_value: bool) -> list[Step]:
    del is_value
    consequence = statement.child_by_field_name("consequence")
    return core.steps(_statements(consequence), _RULES) if consequence else []


def _else_steps(statement: Node, *, is_value: bool) -> list[Step] | None:
    del is_value
    alternative = statement.child_by_field_name("alternative")
    return None if alternative is None else core.steps(_statements(alternative), _RULES)


def _switch_subject(statement: Node) -> str:
    subject = statement.child_by_field_name("value")
    return _flat(text_of(subject)) if subject is not None else ""


def _switch_clauses(statement: Node) -> list[core.Clause]:
    body = statement.child_by_field_name("body")
    sections = [c for c in body.named_children if c.type == "switch_section"] if body else []
    clauses: list[core.Clause] = []
    for section in sections:
        heads = [c for c in section.named_children if _is_head(c)]
        nodes = [c for c in section.named_children if not _is_head(c) and c.type != "comment"]
        # 本体の無いセクションは次のセクションへ落ちる
        clauses.append(
            core.Clause(
                _head_text(section, heads),
                tuple(nodes),
                falls_through=not nodes and section is not sections[-1],
            )
        )
    return clauses


def _is_head(node: Node) -> bool:
    return node.type.endswith("_pattern") or node.type in _NON_BODY


def _head_text(section: Node, heads: list[Node]) -> str:
    # 先頭のパターンから when の条件の末尾までの原文。パターンが無いセクションは default
    if not heads:
        return "default"
    start = heads[0].start_byte - section.start_byte
    end = heads[-1].end_byte - section.start_byte
    return _flat(required(section.text)[start:end].decode())


def _switch_expression(expression: Node) -> Branch:
    named = expression.named_children
    subject_text = _flat(text_of(named[0])) if named else ""
    arms: list[BranchArm] = []
    for arm in named:
        if arm.type != "switch_expression_arm":
            # 対象の式。腕ではない
            continue
        children = arm.named_children
        label = _flat(required(arm.text)[: children[-1].start_byte - arm.start_byte].decode())
        # 腕の `=>` の左を呼び名にする。右が throw の式なら値なしの終わり
        value = children[-1]
        step = (
            Return(None)
            if value.type == "throw_expression"
            else Return(core.member_text(value, _RULES))
        )
        arms.append(BranchArm(label.removesuffix("=>").strip(), (step,)))
    return Branch(f"switch {subject_text}".strip(), tuple(arms), None)


_RULES = core.Rules(
    member_nodes=frozenset({"member_access_expression"}),
    member_pattern=_MEMBER_REFERENCE,
    statement_readers={
        "return_statement": _return_statement,
        "if_statement": _if_statement,
        "switch_statement": _switch_statement,
        "block": _block,
    },
    is_throw=lambda statement: statement.type == "throw_statement",
    condition_text=_condition_text,
    then_steps=_then_steps,
    else_steps=_else_steps,
    switch_subject=_switch_subject,
    switch_clauses=_switch_clauses,
    arm_name=core.joined_case_label,
    bundles_fallthrough=True,
)
