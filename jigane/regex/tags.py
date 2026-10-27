"""数えた値を閾値に当てて、利用者に見せる札にする。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from jigane.gates.explanation import GateExplanation
from jigane.languages import registry
from jigane.languages.base import RegexLiteral, RegexUse
from jigane.regex.metrics import RegexMetrics, measure
from jigane.regex.syntax import Unreadable, parse


class Rule(StrEnum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"
    R6 = "R6"
    R7 = "R7"


class ShardGroup(StrEnum):
    """断片の群。規則の札をどの群の断片へ渡すかを表す。"""

    MICRO = "micro"
    SECURITY = "security"


@dataclass(frozen=True)
class RuleDefinition:
    """規則の付属の情報。short_name は札のセルに並べる短い名で、値のある規則は {n} に値が入る。

    explanation の三つの文も、値のある規則（R1〜R5）は {n} を含みうる。
    値は explain が札の値で埋める。explanation は利用者に見せる敬体の文で、
    material_direction は方針の資料に入れる常体の直し方。資料の文は評価の結果を動かさないため、
    表示の文とは別に持つ。
    """

    short_name: str
    shard_group: ShardGroup
    explanation: GateExplanation
    material_direction: str


# 実装には読みやすさの規則（R1〜R6）、セキュリティには時間が爆発する形（R7）だけを渡す。
# Rule の全値を覆うことは tests/test_regex_tags.py が確かめる
RULE_DEFINITIONS: dict[Rule, RuleDefinition] = {
    Rule.R1: RuleDefinition(
        "重み {n}",
        ShardGroup.MICRO,
        GateExplanation(
            "正規表現が複雑だと、読み手が何に一致するのかを追えず、直すたびに壊しやすくなります。",
            "この正規表現の複雑さの重みが {n} で、閾値を超えています。",
            "一部を普通のコードに置き換えるか、複数の正規表現に分けてください。",
        ),
        "一部を普通のコードに置き換えるか、複数の正規表現に分ける。",
    ),
    Rule.R2: RuleDefinition(
        "分岐 {n}",
        ShardGroup.MICRO,
        GateExplanation(
            "正規表現の中の分岐が多いと、どの場合がどの一致になるのかを一目で読めません。",
            "この正規表現は {n} 通りに分岐しています。",
            "分岐はプログラム言語の側へ移し、正規表現ごとに分けて `if` や表で振り分けてください。",
        ),
        "分岐はプログラム言語の側へ移し、正規表現ごとに分けて `if` や表で振り分ける。",
    ),
    Rule.R3: RuleDefinition(
        "条件 {n}",
        ShardGroup.MICRO,
        GateExplanation(
            "先読みと後読みを重ねると、条件どうしの関係を頭の中で組み合わせないと読めません。",
            "この正規表現は先読みと後読みの条件を {n} 個重ねています。",
            "条件はコードで一つずつ確かめてください。",
        ),
        "条件はコードで一つずつ確かめる。",
    ),
    Rule.R4: RuleDefinition(
        "入れ子 {n} 段",
        ShardGroup.MICRO,
        GateExplanation(
            "組と量指定子が深く入れ子になると、どの繰り返しがどこまで及ぶのかを読み取れません。",
            "この正規表現は組と量指定子が {n} 段に入れ子になっています。",
            "内側を名前のある正規表現に分けてください。",
        ),
        "内側を名前のある正規表現に分ける。",
    ),
    Rule.R5: RuleDefinition(
        "捕獲 {n} 個",
        ShardGroup.MICRO,
        GateExplanation(
            "名前の無い捕獲が多いと、番号と意味の対応を数えて読むことになり、取り違えやすくなります。",
            "この正規表現は名前の無い捕獲を {n} 個持っています。",
            "名前つきの組にするか、捕獲した後の処理をコードで書いてください。",
        ),
        "名前つきの組にするか、捕獲した後の処理をコードで書く。",
    ),
    Rule.R6: RuleDefinition(
        "正規表現でなくてよい",
        ShardGroup.MICRO,
        GateExplanation(
            "文字列そのものを探すだけなのに正規表現を使うと、読み手が特殊な文字の有無を確かめる手間を負います。",
            "この正規表現の本文には特殊な文字がありません。",
            "文字列の置き換えや比較のメソッドを使ってください。",
        ),
        "文字列の置き換えや比較のメソッドを使う。",
    ),
    Rule.R7: RuleDefinition(
        "バックトラック",
        ShardGroup.SECURITY,
        GateExplanation(
            "入れ子の量指定子は、一致しない入力で照合の時間が爆発し、処理を止める攻撃の足がかりになりえます。",
            "この正規表現は入れ子の量指定子を持っています。",
            "量指定子を外すか、占有の形や原子の組を使ってください。",
        ),
        "量指定子を外すか、占有の形や原子の組を使う。",
    ),
}


class Severity(StrEnum):
    NIT = "nit"
    WARNING = "warning"


@dataclass(frozen=True)
class CountedTag:
    """数を測る規則（R1〜R5）の札。閾値に当てた値を持つ。"""

    rule: Rule
    severity: Severity
    count: int

    @property
    def message(self) -> str:
        return _message(self)


@dataclass(frozen=True)
class ShapeTag:
    """形を見つける規則（R6、R7）の札。測った値は無い。"""

    rule: Rule
    severity: Severity

    @property
    def message(self) -> str:
        return _message(self)


RegexTag = CountedTag | ShapeTag


@dataclass(frozen=True)
class CountedRegex:
    """数えられた正規表現。"""

    metrics: RegexMetrics
    tags: tuple[RegexTag, ...]


@dataclass(frozen=True)
class UnreadableRegex:
    """読めなかった正規表現。読めない位置と理由だけを持つ。"""

    unreadable: Unreadable


RegexVerdict = CountedRegex | UnreadableRegex


# 規則ごとの閾値。R2 の nit には「選択の入れ子」、R3 の warning には「条件つきの組」も加わる
COMPLEXITY_NIT = 16
COMPLEXITY_WARNING = 21
BRANCHES_NIT = 5
BRANCHES_WARNING = 8
LOOKAROUND_NIT = 2
LOOKAROUND_WARNING = 3
NESTING_NIT = 4
NESTING_WARNING = 5
UNNAMED_CAPTURES_NIT = 4
UNNAMED_CAPTURES_WARNING = 6


def explain(tag: RegexTag) -> GateExplanation:
    """札の規則の三つの文を返す。値のある札（R1〜R5）は札の値を {n} に埋める。"""
    explanation = RULE_DEFINITIONS[tag.rule].explanation
    match tag:
        case CountedTag(count=count):
            return GateExplanation(
                explanation.concern.format(n=count),
                explanation.violation.format(n=count),
                explanation.direction.format(n=count),
            )
        case ShapeTag():
            # R6、R7 の文は {n} を含まないので、埋めるものが無い
            return explanation
        case _ as unreachable:
            assert_never(unreachable)


def _message(tag: RegexTag) -> str:
    """表や断片に出す一続きの文。なぜ違反かと直す向きをつなぐ。"""
    explanation = explain(tag)
    return explanation.violation + explanation.direction


def _counted(rule: Rule, severity: Severity, count: int) -> CountedTag:
    return CountedTag(rule, severity, count)


def _shape(rule: Rule, severity: Severity) -> ShapeTag:
    return ShapeTag(rule, severity)


def _severity(*, is_warning: bool) -> Severity:
    return Severity.WARNING if is_warning else Severity.NIT


def _tags_for(metrics: RegexMetrics, use: RegexUse, *, in_test_file: bool) -> tuple[RegexTag, ...]:
    tags: list[RegexTag] = []
    if metrics.complexity >= COMPLEXITY_NIT:
        warning = metrics.complexity >= COMPLEXITY_WARNING
        tags.append(_counted(Rule.R1, _severity(is_warning=warning), metrics.complexity))
    if metrics.max_branches >= BRANCHES_NIT or metrics.nested_alternation:
        warning = metrics.max_branches >= BRANCHES_WARNING
        tags.append(_counted(Rule.R2, _severity(is_warning=warning), metrics.max_branches))
    if metrics.lookaround_count >= LOOKAROUND_NIT or metrics.has_conditional:
        warning = metrics.lookaround_count >= LOOKAROUND_WARNING or metrics.has_conditional
        count = metrics.lookaround_count + (1 if metrics.has_conditional else 0)
        tags.append(_counted(Rule.R3, _severity(is_warning=warning), count))
    if metrics.max_nesting >= NESTING_NIT:
        warning = metrics.max_nesting >= NESTING_WARNING
        tags.append(_counted(Rule.R4, _severity(is_warning=warning), metrics.max_nesting))
    if metrics.unnamed_captures >= UNNAMED_CAPTURES_NIT:
        warning = metrics.unnamed_captures >= UNNAMED_CAPTURES_WARNING
        tags.append(_counted(Rule.R5, _severity(is_warning=warning), metrics.unnamed_captures))
    # COMPILE は値を作るだけで、文字列のメソッドに置き換えられない使い方がある
    # 試験のファイルは正規表現の API そのものを試すので、置き換えの提案は誤りになる
    if metrics.no_special_chars and use is not RegexUse.COMPILE and not in_test_file:
        tags.append(_shape(Rule.R6, Severity.NIT))
    if metrics.catastrophic_forms:
        tags.append(_shape(Rule.R7, Severity.WARNING))
    return tuple(tags)


def judge(literal: RegexLiteral) -> RegexVerdict:
    """正規表現一つを数えて札を付ける。読めない正規表現は数えず、理由だけを返す。"""
    tree = parse(literal.pattern, literal.dialect)
    if isinstance(tree, Unreadable):
        return UnreadableRegex(tree)
    metrics = measure(tree)
    # 試験のファイルかどうかは path から導かれる事実なので、書き場所の項目にせずここで導く
    in_test_file = registry.is_test_path(literal.path, registry.adapters())
    return CountedRegex(metrics, _tags_for(metrics, literal.use, in_test_file=in_test_file))
