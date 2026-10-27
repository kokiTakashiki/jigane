from __future__ import annotations

import pytest

from jigane.languages.base import RegexDialect, RegexLiteral, RegexUse
from jigane.regex.tags import (
    RULE_DEFINITIONS,
    CountedRegex,
    CountedTag,
    RegexTag,
    Rule,
    Severity,
    ShapeTag,
    UnreadableRegex,
    explain,
    judge,
)

STRENGTH_RULE = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^\w\s]).{8,64}$"


def literal(pattern: str, use: RegexUse = RegexUse.COMPILE) -> RegexLiteral:
    return RegexLiteral(
        pattern=pattern, dialect=RegexDialect.PYTHON, call="re.x", use=use, path="a.py", line=1
    )


def tags_of(pattern: str, use: RegexUse = RegexUse.COMPILE) -> tuple[RegexTag, ...]:
    verdict = judge(literal(pattern, use))
    assert isinstance(verdict, CountedRegex)
    return verdict.tags


def severity_of(pattern: str, rule: Rule, use: RegexUse = RegexUse.COMPILE) -> Severity | None:
    for tag in tags_of(pattern, use):
        if tag.rule is rule:
            return tag.severity
    return None


def test_date_has_no_tag() -> None:
    verdict = judge(literal(r"^\d{4}-\d{2}-\d{2}$"))
    assert isinstance(verdict, CountedRegex)
    assert verdict.tags == ()
    assert verdict.metrics.complexity == 3


def test_password_tags() -> None:
    by_rule = {tag.rule: tag for tag in tags_of(STRENGTH_RULE)}
    assert by_rule[Rule.R1].severity is Severity.NIT
    assert "重みが 16" in by_rule[Rule.R1].message
    assert by_rule[Rule.R3].severity is Severity.WARNING
    assert "4 個" in by_rule[Rule.R3].message


def test_nested_quantifier_is_warning() -> None:
    assert [(tag.rule, tag.severity) for tag in tags_of("^(a+)+$")] == [
        (Rule.R7, Severity.WARNING)
    ]


@pytest.mark.parametrize("pattern", [r"(?:/[\w.]+)*", "(-[A-Z]+)*"])
def test_separator_repetition_has_no_r7(pattern: str) -> None:
    assert severity_of(pattern, Rule.R7) is None


def test_overlapping_alternation_is_warning() -> None:
    assert severity_of("(a|ab)*", Rule.R7) is Severity.WARNING


def alternatives(count: int) -> str:
    return "|".join("abcdefghij"[:count])


def lookaheads(count: int) -> str:
    return "(?=a)" * count


@pytest.mark.parametrize(
    ("pattern", "rule", "expected"),
    [
        # R1: 15 は札なし、16 が nit、21 が warning
        (STRENGTH_RULE.replace(r"[^\w\s]", r"\w"), Rule.R1, None),
        (STRENGTH_RULE, Rule.R1, Severity.NIT),
        (STRENGTH_RULE + "[a][b][c][d]", Rule.R1, Severity.NIT),
        (STRENGTH_RULE + "[a][b][c][d][e]", Rule.R1, Severity.WARNING),
        # R2: 選択肢 4 は札なし、5 が nit、7 が nit、8 が warning、入れ子も nit
        (alternatives(4), Rule.R2, None),
        (alternatives(5), Rule.R2, Severity.NIT),
        (alternatives(7), Rule.R2, Severity.NIT),
        (alternatives(8), Rule.R2, Severity.WARNING),
        ("x(a|b)|c", Rule.R2, Severity.NIT),
        # R3: 1 つは札なし、2 つが nit、3 つが warning、条件つきの組が warning
        (lookaheads(1), Rule.R3, None),
        (lookaheads(2), Rule.R3, Severity.NIT),
        (lookaheads(3), Rule.R3, Severity.WARNING),
        ("(a)?(?(1)b|c)", Rule.R3, Severity.WARNING),
        # R4: 3 段は札なし、4 段が nit、5 段が warning
        ("(((a)))", Rule.R4, None),
        ("((((a))))", Rule.R4, Severity.NIT),
        ("(((((a)))))", Rule.R4, Severity.WARNING),
        # R5: 3 つは札なし、4 つが nit、5 つが nit、6 つが warning
        ("(a)(b)(c)", Rule.R5, None),
        ("(a)(b)(c)(d)", Rule.R5, Severity.NIT),
        ("(a)(b)(c)(d)(e)", Rule.R5, Severity.NIT),
        ("(a)(b)(c)(d)(e)(f)", Rule.R5, Severity.WARNING),
    ],
)
def test_threshold_boundaries(pattern: str, rule: Rule, expected: Severity | None) -> None:
    assert severity_of(pattern, rule) is expected


@pytest.mark.parametrize("use", [RegexUse.MATCH, RegexUse.REPLACE, RegexUse.SPLIT])
def test_literal_text_in_target_use_is_nit(use: RegexUse) -> None:
    assert severity_of(r"foo\.bar", Rule.R6, use) is Severity.NIT


def test_literal_text_in_compile_is_not_tagged() -> None:
    assert severity_of(r"foo\.bar", Rule.R6, RegexUse.COMPILE) is None


def test_special_text_in_target_call_is_not_tagged() -> None:
    assert severity_of("fo+", Rule.R6, RegexUse.REPLACE) is None


def test_r6_message_has_no_count() -> None:
    (tag,) = tags_of("foo", RegexUse.REPLACE)
    assert isinstance(tag, ShapeTag)
    assert tag.message.startswith("この正規表現の本文には特殊な文字がありません。")


def test_unreadable_pattern() -> None:
    verdict = judge(literal("(a"))
    assert isinstance(verdict, UnreadableRegex)
    assert verdict.unreadable.reason


def test_counted_rule_tag_carries_count() -> None:
    (tag,) = [t for t in tags_of("(a)(b)(c)(d)") if t.rule is Rule.R5]
    assert isinstance(tag, CountedTag)
    assert tag.count == 4


def test_rule_definitions_cover_every_rule() -> None:
    assert set(RULE_DEFINITIONS) == set(Rule)


def test_r6_skips_test_file() -> None:
    for path, expected in (("a.py", True), ("tests/test_a.py", False), ("src/test_a.py", False)):
        plain = RegexLiteral(
            pattern="foo",
            dialect=RegexDialect.PYTHON,
            call="re.sub",
            use=RegexUse.REPLACE,
            path=path,
            line=1,
        )
        verdict = judge(plain)
        assert isinstance(verdict, CountedRegex)
        assert any(tag.rule is Rule.R6 for tag in verdict.tags) is expected


def test_judge_takes_no_test_file_argument() -> None:
    with pytest.raises(TypeError):
        judge(literal("foo"), in_test_file=True)  # type: ignore[call-arg]


@pytest.mark.parametrize("rule", list(Rule))
def test_every_rule_has_three_nonempty_sentences(rule: Rule) -> None:
    explanation = RULE_DEFINITIONS[rule].explanation
    assert explanation.concern.strip()
    assert explanation.violation.strip()
    assert explanation.direction.strip()
    tag = (
        ShapeTag(rule, Severity.NIT)
        if rule in (Rule.R6, Rule.R7)
        else CountedTag(rule, Severity.NIT, 7)
    )
    filled = explain(tag)
    assert "{n}" not in filled.text
    assert not any(mark in filled.text for mark in ("・", "—", "―", "–"))


def test_counted_tag_value_fills_the_sentences() -> None:
    explanation = explain(CountedTag(Rule.R5, Severity.NIT, 9))
    assert "9 個" in explanation.violation
    assert "{n}" not in explanation.text


def test_shape_tag_returns_the_definition_sentences() -> None:
    assert explain(ShapeTag(Rule.R7, Severity.WARNING)) == RULE_DEFINITIONS[Rule.R7].explanation


def test_message_is_derived_from_the_explanation() -> None:
    for tag in (CountedTag(Rule.R2, Severity.NIT, 6), ShapeTag(Rule.R6, Severity.NIT)):
        explanation = explain(tag)
        assert tag.message == explanation.violation + explanation.direction
