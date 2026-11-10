"""判別の流れ図の部品が共有する、言語に依らない部分。

言語ごとの部品（`swift_discriminators`、`java_discriminators`、`csharp_discriminators`）は、
文の節点の種類の見分け方、条件や case の原文の取り方、式としての if と switch だけを持つ。
ここは、文の並びを Return と Branch に振り分ける骨格、if の連なりと switch の Branch の組み立て、
throw を値なしの Return にする扱い、return の値の絞り込みを持つ。
言語ごとの差し込みは `Rules` にまとめ、関数は入力の節点と `Rules` を引数で受ける。
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from tree_sitter import Node

from jigane.languages.base import Branch, BranchArm, Return, Step
from jigane.preconditions.required import required


class StatementReader(Protocol):
    """文、その後ろの文の並び、式が値を返す位置か、を受けて Step の並びを返す。"""

    def __call__(self, statement: Node, rest: list[Node], /, *, is_value: bool) -> list[Step]: ...


class ValueReader(Protocol):
    """式の文と、式が値を返す位置か、を受けて暗黙の Return を返す。読まない文は None。"""

    def __call__(self, statement: Node, /, *, is_value: bool) -> Step | None: ...


class ThenSteps(Protocol):
    def __call__(self, statement: Node, /, *, is_value: bool) -> list[Step]: ...


class ElseSteps(Protocol):
    def __call__(self, statement: Node, /, *, is_value: bool) -> list[Step] | None: ...


@dataclass(frozen=True)
class Clause:
    """switch の腕の元になる節。label は呼び名の一つ分、nodes は本体の文。"""

    label: str
    nodes: tuple[Node, ...]
    # 本体が無く、次の節へ落ちる。`Rules.bundles_fallthrough` が真のときだけ次の腕の名前に束ねる
    falls_through: bool = False


@dataclass(frozen=True)
class Rules:
    """言語ごとの差し込み。"""

    # return の値に残す節点の種類と、原文が合うべき正規表現。渡さなければ原文の形は絞らない
    member_nodes: frozenset[str]
    member_pattern: re.Pattern[str] | None
    # 文の節点の種類から、その文を Step の並びにする関数への対応
    statement_readers: Mapping[str, StatementReader]
    # throw の文か。真なら値なしの Return にする
    is_throw: Callable[[Node], bool]
    condition_text: Callable[[Node], str]
    # if の真の腕と、else の腕の Step。else が無ければ None
    then_steps: ThenSteps
    else_steps: ElseSteps
    switch_subject: Callable[[Node], str]
    switch_clauses: Callable[[Node], Sequence[Clause]]
    # 束ねた節の呼び名の並びから、腕の名前を作る
    arm_name: Callable[[Sequence[str]], str]
    # 本体の無い腕を次の腕に束ねるか。束ねない言語は、本体の無い腕を空の腕にする
    bundles_fallthrough: bool
    # 読んだ後に後ろの文を読まない文の種類。後ろの文はその文の読み手が受け持つ
    ends_sequence: frozenset[str] = field(default_factory=frozenset)
    # 読み手の無い文を、値を返す位置で暗黙の Return として読む関数
    value_reader: ValueReader | None = None


def member_text(expression: Node | None, rules: Rules) -> str | None:
    """メンバ参照だけを残す。それ以外の式は値なしの終わりとして None を返す。"""
    if expression is None or expression.type not in rules.member_nodes:
        return None
    text = required(expression.text).decode()
    if rules.member_pattern is not None and not rules.member_pattern.match(text):
        return None
    return text


def joined_case_label(labels: Sequence[str]) -> str:
    """束ねた case の呼び名。`default` だけは `case` を付けない。"""
    return "default" if list(labels) == ["default"] else "case " + ", ".join(labels)


def steps(statements: list[Node], rules: Rules, *, is_value: bool = False) -> list[Step]:
    """文の並びから Return と Branch だけを残す。is_value は式が値を返す位置であることを示す。"""
    result: list[Step] = []
    for index, statement in enumerate(statements):
        reader = rules.statement_readers.get(statement.type)
        if rules.is_throw(statement):
            result.append(Return(None))
        elif reader is not None:
            result.extend(reader(statement, statements[index + 1 :], is_value=is_value))
            if statement.type in rules.ends_sequence:
                break
        elif (
            rules.value_reader is not None
            and (value := rules.value_reader(statement, is_value=is_value)) is not None
        ):
            result.append(value)
        else:
            # 他の文は分岐も終わりも作らない。ループや try の中の分岐は読まない
            continue
    return result


def if_branch(statement: Node, rules: Rules, *, is_value: bool = False) -> Branch:
    """偽の腕に else if の連なりを入れ子にして入れる。else が無ければ fallthrough の no。"""
    text = rules.condition_text(statement)
    arms = [BranchArm("yes", tuple(rules.then_steps(statement, is_value=is_value)))]
    otherwise = rules.else_steps(statement, is_value=is_value)
    if otherwise is None:
        return Branch(text, tuple(arms), "no")
    arms.append(BranchArm("no", tuple(otherwise)))
    return Branch(text, tuple(arms), None)


def switch_branch(statement: Node, rules: Rules, *, is_value: bool = False) -> Branch:
    arms: list[BranchArm] = []
    labels: list[str] = []
    for clause in rules.switch_clauses(statement):
        labels.append(clause.label)
        if rules.bundles_fallthrough and clause.falls_through:
            continue
        body = tuple(steps(list(clause.nodes), rules, is_value=is_value))
        arms.append(BranchArm(rules.arm_name(labels), body))
        labels = []
    return Branch(f"switch {rules.switch_subject(statement)}".strip(), tuple(arms), None)
