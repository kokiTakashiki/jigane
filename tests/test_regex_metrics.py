from __future__ import annotations

import pytest

from jigane.languages.base import RegexDialect
from jigane.regex.metrics import CatastrophicForm, RegexMetrics, measure
from jigane.regex.syntax import Unreadable, parse

STRENGTH_RULE = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^\w\s]).{8,64}$"


def metrics_of(pattern: str, dialect: RegexDialect = RegexDialect.PYTHON) -> RegexMetrics:
    tree = parse(pattern, dialect)
    assert not isinstance(tree, Unreadable)
    return measure(tree)


def test_date_complexity() -> None:
    assert metrics_of(r"^\d{4}-\d{2}-\d{2}$").complexity == 3


def test_password_condition() -> None:
    metrics = metrics_of(STRENGTH_RULE)
    assert metrics.complexity == 16
    assert metrics.lookaround_count == 4
    assert not metrics.has_conditional


@pytest.mark.parametrize(
    ("pattern", "complexity"),
    [
        (r"\d", 0),  # 単独の既定のクラスは文字クラスに数えない
        (r"[a-z]", 1),
        (r"(a)\1", 1),  # 後方参照は 1、ただの組は 0
        (r"(a|b)", 2),  # 選択は深さ 1 に、二つ目の `|` の 1 を足す
        (r"(?i:a)", 1),  # 旗つきの組は深さの点
        (r"(a+)+", 3),
    ],
)
def test_complexity_weights(pattern: str, complexity: int) -> None:
    assert metrics_of(pattern).complexity == complexity


def test_branches() -> None:
    assert metrics_of("a|b|c").max_branches == 3
    assert not metrics_of("a|b|c").nested_alternation
    nested = metrics_of("x(a|b)|c")
    assert nested.max_branches == 2
    assert nested.nested_alternation
    assert metrics_of("abc").max_branches == 0


def test_conditional() -> None:
    assert metrics_of("(a)?(?(1)b|c)").has_conditional


def test_nesting_and_captures() -> None:
    metrics = metrics_of("(?:(a)(?P<n>b))+")
    assert metrics.max_nesting == 2
    assert metrics.unnamed_captures == 1


def test_quantified_group_is_one_level() -> None:
    brace = r"\{(\d+)(?:(,)(\d*))?\}|\{,(\d+)\}"
    assert metrics_of(brace).max_nesting == 3


def test_quantifier_without_group_is_one_level() -> None:
    assert metrics_of(r"\d+").max_nesting == 1
    assert metrics_of(r"(a)").max_nesting == 1


def test_nested_quantifiers_show_as_levels() -> None:
    assert metrics_of("(a+)+").max_nesting == 2
    assert metrics_of("((a+)+)+").max_nesting == 3


def test_nested_quantifier_form() -> None:
    assert metrics_of("^(a+)+$").catastrophic_forms == (CatastrophicForm.NESTED_QUANTIFIER,)
    assert metrics_of(r"(\w+\s?)+").catastrophic_forms == (CatastrophicForm.NESTED_QUANTIFIER,)


@pytest.mark.parametrize("pattern", [r"(?:/[\w.]+)*", "(-[A-Z]+)*", "(a+){1,3}", "(ab)*"])
def test_repetition_with_separator_is_not_flagged(pattern: str) -> None:
    assert metrics_of(pattern).catastrophic_forms == ()


def test_disjoint_separator_is_not_flagged() -> None:
    swift = r"(?:(public|open|internal)\s+)*"
    assert metrics_of(swift, RegexDialect.SWIFT).catastrophic_forms == ()
    assert metrics_of(r"(\w+\s+)+").catastrophic_forms == ()


@pytest.mark.parametrize("pattern", [r"(\w+\s?)+", r"(\w+\s*)+", r"(\w+\d+)+", "^(a+)+$"])
def test_overlapping_or_optional_separator_is_flagged(pattern: str) -> None:
    assert metrics_of(pattern).catastrophic_forms == (CatastrophicForm.NESTED_QUANTIFIER,)


def test_overlapping_alternation_form() -> None:
    assert metrics_of("(a|ab)*").catastrophic_forms == (CatastrophicForm.OVERLAPPING_ALTERNATION,)
    assert metrics_of("(a|b)*").catastrophic_forms == ()


def test_literal_only() -> None:
    assert metrics_of(r"foo\.bar").no_special_chars
    assert not metrics_of("fo+").no_special_chars
    assert not metrics_of("").no_special_chars
    assert not metrics_of("a.b").no_special_chars


@pytest.mark.parametrize("pattern", [r"([a-z]-+)+", r"((a|[b])-+)+"])
def test_separator_after_an_undecidable_edge_is_flagged(pattern: str) -> None:
    # 文字クラスは中身を持たず、選択肢の一つでも決められなければ全体が決められない
    assert metrics_of(pattern).catastrophic_forms == (CatastrophicForm.NESTED_QUANTIFIER,)


def test_separator_after_a_decidable_alternation_is_not_flagged() -> None:
    assert metrics_of(r"((a|b)-+)+").catastrophic_forms == ()


def test_conditional_without_else_branch() -> None:
    metrics = metrics_of("(a)?(?(1)b)")
    assert metrics.has_conditional
    assert metrics.complexity == 2
    assert metrics.max_nesting == 1
