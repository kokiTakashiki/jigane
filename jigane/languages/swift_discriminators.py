"""Swift の関数の本体から、判別の流れ図に渡す分岐と return を読む。

読む文は if（else if、else）、guard、switch（case、default）、return、throw。
return の値は `.foo` と `Kind.foo` のメンバ参照だけを残す。
式としての if と switch（Swift 5.9）は、return の式のときと、本体がその一文だけのときに読む。
変数への代入の右辺に書いた式は読まない。
"""

import re

from tree_sitter import Node

from jigane.languages import discriminators_core as core
from jigane.languages.base import Branch, BranchArm, Return, Step
from jigane.languages.node_text import text_of
from jigane.preconditions.required import required

_MEMBER_REFERENCE = re.compile(r"^(\.[A-Za-z_]\w*|[A-Za-z_]\w*(\.[A-Za-z_]\w*)+)$")
_MEMBER_NODES = frozenset({"prefix_expression", "navigation_expression"})
_CONDITION_END = frozenset({"statements", "else"})
_TYPED_DECLARATIONS = frozenset({"function_declaration", "property_declaration"})
_TYPE_NODES = frozenset({"user_type", "optional_type"})


def _statements(node: Node) -> list[Node]:
    container = (
        node
        if node.type == "statements"
        else next((c for c in node.children if c.type == "statements"), None)
    )
    return list(container.named_children) if container is not None else []


def _condition_text(statement: Node) -> str:
    # 条件は let の束縛やカンマ区切りで複数の節点に分かれるので、先頭から末尾までの原文を取る
    nodes: list[Node] = []
    for child in statement.children:
        if child.type in _CONDITION_END:
            break
        if child.is_named:
            nodes.append(child)
    if not nodes:
        return "?"
    start = nodes[0].start_byte - statement.start_byte
    end = nodes[-1].end_byte - statement.start_byte
    return " ".join(required(statement.text)[start:end].decode().split())


def discriminator_steps(body_node: Node) -> list[Step]:
    """本体の分岐と return を文書順に返す。"""
    statements = _statements(body_node)
    owner = _return_type(body_node)
    # 戻り値の型を持つ関数の本体が if か switch の一文なら、腕の式を暗黙の return と読む
    steps = core.steps(statements, _RULES, is_value=owner is not None and len(statements) == 1)
    return steps if owner is None else _qualify(steps, owner)


def _return_type(body_node: Node) -> str | None:
    """本体を持つ関数や計算プロパティの戻り値の型の名前。`?` と `!` は外す。"""
    declaration = body_node.parent
    while declaration is not None and declaration.type not in _TYPED_DECLARATIONS:
        declaration = declaration.parent
    if declaration is None:
        return None
    if declaration.type == "property_declaration":
        annotation = next((c for c in declaration.children if c.type == "type_annotation"), None)
        types = [] if annotation is None else list(annotation.named_children)
    else:
        types = [c for c in declaration.named_children if c.type in _TYPE_NODES]
    if not types:
        return None
    name = text_of(types[-1]).rstrip("?!")
    return None if "<" in name else name


def _qualify(steps: list[Step], owner: str) -> list[Step]:
    """`.foo` を `Owner.foo` に直す。戻り値の型が分かれば、暗黙のメンバ参照の列挙型が決まる。"""
    qualified: list[Step] = []
    for step in steps:
        if isinstance(step, Return):
            value = step.value
            qualified.append(Return(owner + value) if value and value.startswith(".") else step)
        else:
            arms = tuple(
                BranchArm(a.label, tuple(_qualify(list(a.steps), owner))) for a in step.arms
            )
            qualified.append(Branch(step.condition, arms, step.fallthrough_label))
    return qualified


def _transfer(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    del is_value
    if not text_of(statement).startswith("return"):
        # break、continue、fallthrough は関数の終わりではない。throw は `Rules.is_throw` が読む
        return []
    result = statement.child_by_field_name("result")
    if result is not None and result.type == "if_statement":
        return [core.if_branch(result, _RULES, is_value=True)]
    if result is not None and result.type == "switch_statement":
        return [core.switch_branch(result, _RULES, is_value=True)]
    return [Return(core.member_text(result, _RULES))]


def _if_statement(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    return [core.if_branch(statement, _RULES, is_value=is_value)]


def _switch_statement(statement: Node, _rest: list[Node], *, is_value: bool) -> list[Step]:
    return [core.switch_branch(statement, _RULES, is_value=is_value)]


def _guard_statement(statement: Node, rest: list[Node], *, is_value: bool) -> list[Step]:
    # 条件が真の流れは、guard の後ろの文がすべて受け持つ
    return [_guard_branch(statement, rest, is_value=is_value)]


def _expression_value(statement: Node, *, is_value: bool) -> Step | None:
    if is_value and statement.type.endswith("_expression"):
        return Return(core.member_text(statement, _RULES))
    return None


def _is_throw(statement: Node) -> bool:
    return statement.type == "control_transfer_statement" and text_of(statement).startswith(
        "throw"
    )


def _then_steps(statement: Node, *, is_value: bool) -> list[Step]:
    children = statement.children
    else_index = next((i for i, c in enumerate(children) if c.type == "else"), None)
    before = children if else_index is None else children[:else_index]
    consequence = next((c for c in before if c.type == "statements"), None)
    return core.steps(_statements(consequence), _RULES, is_value=is_value) if consequence else []


def _else_steps(statement: Node, *, is_value: bool) -> list[Step] | None:
    children = statement.children
    else_index = next((i for i, c in enumerate(children) if c.type == "else"), None)
    if else_index is None:
        return None
    alternative = children[else_index + 1 :]
    nested = next((c for c in alternative if c.type == "if_statement"), None)
    if nested is not None:
        return [core.if_branch(nested, _RULES, is_value=is_value)]
    return core.steps(
        [s for c in alternative if c.type == "statements" for s in _statements(c)],
        _RULES,
        is_value=is_value,
    )


def _guard_branch(statement: Node, rest: list[Node], *, is_value: bool) -> Branch:
    """偽の腕は else の中身、真の腕は guard の後ろの文。"""
    otherwise = [s for c in statement.children if c.type == "statements" for s in _statements(c)]
    return Branch(
        _condition_text(statement),
        (
            BranchArm("yes", tuple(core.steps(rest, _RULES, is_value=is_value))),
            BranchArm("no", tuple(core.steps(otherwise, _RULES))),
        ),
        None,
    )


def _switch_subject(statement: Node) -> str:
    subject = statement.child_by_field_name("expr")
    return " ".join(text_of(subject).split()) if subject is not None else ""


def _switch_clauses(statement: Node) -> list[core.Clause]:
    clauses: list[core.Clause] = []
    for entry in statement.named_children:
        if entry.type != "switch_entry":
            # 対象の式。腕ではない
            continue
        body = next((c for c in entry.named_children if c.type == "statements"), None)
        nodes = tuple(_statements(body)) if body else ()
        clauses.append(core.Clause(_case_label(entry), nodes, falls_through=not nodes))
    return clauses


def _case_label(entry: Node) -> str:
    if any(c.type == "default_keyword" for c in entry.children):
        return "default"
    # パターンと where 節の原文。本体の statements は含めない
    heads = [c for c in entry.named_children if c.type != "statements"]
    if not heads:
        return "case"
    start = heads[0].start_byte - entry.start_byte
    end = heads[-1].end_byte - entry.start_byte
    return "case " + " ".join(required(entry.text)[start:end].decode().split())


_RULES = core.Rules(
    member_nodes=_MEMBER_NODES,
    member_pattern=_MEMBER_REFERENCE,
    statement_readers={
        "control_transfer_statement": _transfer,
        "if_statement": _if_statement,
        "switch_statement": _switch_statement,
        "guard_statement": _guard_statement,
    },
    is_throw=_is_throw,
    condition_text=_condition_text,
    then_steps=_then_steps,
    else_steps=_else_steps,
    switch_subject=_switch_subject,
    switch_clauses=_switch_clauses,
    # 本体の無い case は束ねず、空の腕にする
    arm_name=lambda labels: labels[0],
    bundles_fallthrough=False,
    ends_sequence=frozenset({"guard_statement"}),
    value_reader=_expression_value,
)
