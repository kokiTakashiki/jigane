"""Swift の関数の本体から、判別の流れ図に渡す分岐と return を読む。

読む文は if（else if、else）、guard、switch（case、default）、return、throw。
return の値は `.foo` と `Kind.foo` のメンバ参照だけを残す。
式としての if と switch（Swift 5.9）は、return の式のときと、本体がその一文だけのときに読む。
変数への代入の右辺に書いた式は読まない。
"""

import re

from tree_sitter import Node

from jigane.languages.base import Branch, BranchArm, Return, Step
from jigane.preconditions.required import required

_MEMBER_REFERENCE = re.compile(r"^(\.[A-Za-z_]\w*|[A-Za-z_]\w*(\.[A-Za-z_]\w*)+)$")
_MEMBER_NODES = frozenset({"prefix_expression", "navigation_expression"})
_CONDITION_END = frozenset({"statements", "else"})
_TYPED_DECLARATIONS = frozenset({"function_declaration", "property_declaration"})
_TYPE_NODES = frozenset({"user_type", "optional_type"})


def _text(node: Node) -> str:
    return required(node.text).decode()


def _member_text(expression: Node | None) -> str | None:
    # 値は Kind.RANGE の形のメンバ参照だけを残す。呼び出しや演算を含む式は値なしの終わりにする
    if expression is None or expression.type not in _MEMBER_NODES:
        return None
    text = _text(expression)
    return text if _MEMBER_REFERENCE.match(text) else None


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
    steps = _steps(statements, is_value=owner is not None and len(statements) == 1)
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
    name = _text(types[-1]).rstrip("?!")
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


def _steps(statements: list[Node], *, is_value: bool) -> list[Step]:
    """文の並びから Return と Branch だけを残す。is_value は式が値を返す位置であることを示す。"""
    steps: list[Step] = []
    for index, statement in enumerate(statements):
        kind = statement.type
        if kind == "control_transfer_statement":
            step = _transfer(statement)
            if step is not None:
                steps.append(step)
        elif kind == "if_statement":
            steps.append(_if_branch(statement, is_value=is_value))
        elif kind == "switch_statement":
            steps.append(_switch_branch(statement, is_value=is_value))
        elif kind == "guard_statement":
            # 条件が真の流れは、guard の後ろの文がすべて受け持つ
            steps.append(_guard_branch(statement, statements[index + 1 :], is_value=is_value))
            break
        elif is_value and kind.endswith("_expression"):
            steps.append(Return(_member_text(statement)))
        else:
            # 他の文は分岐も終わりも作らない。ループや do の中の分岐と、代入の右辺の式は読まない
            continue
    return steps


def _transfer(statement: Node) -> Step | None:
    text = _text(statement)
    if text.startswith("throw"):
        return Return(None)
    if not text.startswith("return"):
        # break、continue、fallthrough は関数の終わりではない
        return None
    result = statement.child_by_field_name("result")
    if result is not None and result.type == "if_statement":
        return _if_branch(result, is_value=True)
    if result is not None and result.type == "switch_statement":
        return _switch_branch(result, is_value=True)
    return Return(_member_text(result))


def _if_branch(statement: Node, *, is_value: bool) -> Branch:
    """偽の腕に、else if の連なりを入れ子にして入れる。"""
    children = statement.children
    else_index = next((i for i, c in enumerate(children) if c.type == "else"), None)
    before = children if else_index is None else children[:else_index]
    consequence = next((c for c in before if c.type == "statements"), None)
    arms = [
        BranchArm(
            "yes",
            tuple(_steps(_statements(consequence), is_value=is_value)) if consequence else (),
        )
    ]
    text = _condition_text(statement)
    if else_index is None:
        return Branch(text, tuple(arms), "no")
    alternative = children[else_index + 1 :]
    nested = next((c for c in alternative if c.type == "if_statement"), None)
    if nested is not None:
        no_steps: list[Step] = [_if_branch(nested, is_value=is_value)]
    else:
        no_steps = _steps(
            [s for c in alternative if c.type == "statements" for s in _statements(c)],
            is_value=is_value,
        )
    arms.append(BranchArm("no", tuple(no_steps)))
    return Branch(text, tuple(arms), None)


def _guard_branch(statement: Node, rest: list[Node], *, is_value: bool) -> Branch:
    """偽の腕は else の中身、真の腕は guard の後ろの文。"""
    otherwise = [s for c in statement.children if c.type == "statements" for s in _statements(c)]
    return Branch(
        _condition_text(statement),
        (
            BranchArm("yes", tuple(_steps(rest, is_value=is_value))),
            BranchArm("no", tuple(_steps(otherwise, is_value=False))),
        ),
        None,
    )


def _switch_branch(statement: Node, *, is_value: bool) -> Branch:
    subject = statement.child_by_field_name("expr")
    arms: list[BranchArm] = []
    for entry in statement.named_children:
        if entry.type != "switch_entry":
            # 対象の式。腕ではない
            continue
        body = next((c for c in entry.named_children if c.type == "statements"), None)
        arms.append(
            BranchArm(
                _case_label(entry),
                tuple(_steps(_statements(body), is_value=is_value)) if body else (),
            )
        )
    subject_text = " ".join(_text(subject).split()) if subject is not None else ""
    return Branch(f"switch {subject_text}".strip(), tuple(arms), None)


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
