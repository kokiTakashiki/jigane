import importlib
import re
from collections.abc import Iterable
from collections.abc import Sequence as AbcSequence
from typing import Any

import pytest

from jigane.languages.base import RegexDialect
from jigane.regex.syntax import (
    Alternation,
    Atom,
    AtomicGroup,
    AtomKind,
    Backreference,
    CaptureGroup,
    CharClass,
    Conditional,
    FlaggedGroup,
    Flags,
    LeadingFlags,
    Look,
    NamedGroup,
    NamedReference,
    NonCaptureGroup,
    NumberedReference,
    Quantifier,
    QuantifierMode,
    Quote,
    RegexNode,
    Sequence,
    Unreadable,
    parse,
)

# 非公開の API で型の情報が無いので、import 文ではなく動的に読み込む。
sre_parser = importlib.import_module("re._parser")

PY = RegexDialect.PYTHON
JAVA = RegexDialect.JAVA
NET = RegexDialect.DOTNET
SWIFT = RegexDialect.SWIFT


def char(text: str) -> Atom:
    return Atom(AtomKind.CHAR, text)


def seq(*items: RegexNode) -> Sequence:
    return Sequence(tuple(items))


A = seq(char("a"))
B = seq(char("b"))


def test_alternation_holds_each_branch_as_one_node() -> None:
    assert parse("a|b", PY) == Alternation((A, B))


def test_plain_sequence() -> None:
    assert parse("ab", PY) == seq(char("a"), char("b"))


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("(a)", CaptureGroup(A)),
        ("(?:a)", NonCaptureGroup(A)),
        ("(?P<n>a)", NamedGroup("n", A)),
        ("(?<n>a)", NamedGroup("n", A)),
        ("(?'n'a)", NamedGroup("n", A)),
        ("(?>a)", AtomicGroup(A)),
    ],
)
@pytest.mark.parametrize("dialect", [PY, JAVA, NET, SWIFT])
def test_group_kinds(pattern: str, expected: RegexNode, dialect: RegexDialect) -> None:
    assert parse(pattern, dialect) == seq(expected)


def test_flagged_group_keeps_flags() -> None:
    assert parse("(?i:a)", PY) == seq(FlaggedGroup(Flags(frozenset("i"), frozenset()), A))
    assert parse("(?i-s:a)", JAVA) == seq(FlaggedGroup(Flags(frozenset("i"), frozenset("s")), A))


@pytest.mark.parametrize(
    ("pattern", "ahead", "negated"),
    [
        ("(?=a)", True, False),
        ("(?!a)", True, True),
        ("(?<=a)", False, False),
        ("(?<!a)", False, True),
    ],
)
def test_lookaround(pattern: str, *, ahead: bool, negated: bool) -> None:
    assert parse(pattern, PY) == seq(Look(ahead, negated, A))


def test_conditional() -> None:
    assert parse("(a)(?(1)b|c)", PY) == seq(
        CaptureGroup(A),
        Conditional(NumberedReference(1), B, seq(char("c"))),
    )
    assert parse("(?<n>a)(?(n)b)", NET) == seq(
        NamedGroup("n", A),
        Conditional(NamedReference("n"), B, None),
    )


@pytest.mark.parametrize(
    ("pattern", "low", "high"),
    [
        ("a*", 0, None),
        ("a+", 1, None),
        ("a?", 0, 1),
        ("a{2}", 2, 2),
        ("a{2,}", 2, None),
        ("a{2,5}", 2, 5),
    ],
)
def test_quantifier_bounds(pattern: str, low: int, high: int | None) -> None:
    assert parse(pattern, PY) == seq(Quantifier(char("a"), low, high, QuantifierMode.GREEDY))


def test_quantifier_lazy_and_possessive() -> None:
    assert parse("a*?", PY) == seq(Quantifier(char("a"), 0, None, QuantifierMode.LAZY))
    assert parse("a++", JAVA) == seq(Quantifier(char("a"), 1, None, QuantifierMode.POSSESSIVE))
    assert parse("a{1,2}+", PY) == seq(Quantifier(char("a"), 1, 2, QuantifierMode.POSSESSIVE))


def test_quantifier_target_is_one_node() -> None:
    group = CaptureGroup(A)
    assert parse("(a)+", PY) == seq(Quantifier(group, 1, None, QuantifierMode.GREEDY))


def test_brace_without_count_is_literal() -> None:
    assert parse("a{x}", JAVA) == seq(char("a"), char("{"), char("x"), char("}"))


def test_character_classes() -> None:
    assert parse("[a-z]", PY) == seq(CharClass(negated=False, has_intersection=False))
    assert parse("[^a-z]", PY) == seq(CharClass(negated=True, has_intersection=False))
    assert parse("[]a]", PY) == seq(CharClass(negated=False, has_intersection=False))
    assert parse(r"[\]a]", PY) == seq(CharClass(negated=False, has_intersection=False))


def test_java_class_intersection() -> None:
    assert parse("[a-z&&[^aeiou]]", JAVA) == seq(CharClass(negated=False, has_intersection=True))
    assert parse("[a-z&&b]", PY) == seq(CharClass(negated=False, has_intersection=False))


def test_backreferences() -> None:
    assert parse(r"(a)\1", PY) == seq(CaptureGroup(A), Backreference(NumberedReference(1)))
    assert parse(r"(?<n>a)\k<n>", JAVA) == seq(
        NamedGroup("n", A), Backreference(NamedReference("n"))
    )
    assert parse("(?P<n>a)(?P=n)", PY) == seq(
        NamedGroup("n", A), Backreference(NamedReference("n"))
    )


def test_atoms() -> None:
    assert parse(r"\n", PY) == seq(Atom(AtomKind.ESCAPE, r"\n"))
    assert parse(r"\x41", PY) == seq(Atom(AtomKind.ESCAPE, r"\x41"))
    assert parse(r"\d", PY) == seq(Atom(AtomKind.CLASS, r"\d"))
    assert parse(r"\p{L}", JAVA) == seq(Atom(AtomKind.PROPERTY, r"\p{L}"))
    assert parse(".", PY) == seq(Atom(AtomKind.ANY, "."))
    assert parse(r"^a$", PY) == seq(
        Atom(AtomKind.ANCHOR, "^"), char("a"), Atom(AtomKind.ANCHOR, "$")
    )
    assert parse(r"\b", PY) == seq(Atom(AtomKind.ANCHOR, r"\b"))


def test_multi_digit_backreference() -> None:
    assert parse(r"(a)\12", PY) == seq(CaptureGroup(A), Backreference(NumberedReference(12)))


def test_named_backreference_delimiters() -> None:
    expected = seq(Backreference(NamedReference("n")))
    assert parse(r"\k{n}", NET) == expected
    assert parse(r"\k'n'", NET) == expected


def test_escape_letters_without_a_following_delimiter_stay_plain_escapes() -> None:
    assert parse(r"\k", PY) == seq(Atom(AtomKind.ESCAPE, r"\k"))
    assert parse(r"\N", PY) == seq(Atom(AtomKind.ESCAPE, r"\N"))
    assert parse(r"\x", PY) == seq(Atom(AtomKind.ESCAPE, r"\x"))


def test_property_escape_forms() -> None:
    assert parse(r"\pL", JAVA) == seq(Atom(AtomKind.PROPERTY, r"\pL"))
    assert parse(r"\p", JAVA) == Unreadable(0, "プロパティの名前が無い")
    assert parse(r"\p{L", JAVA) == Unreadable(3, "プロパティが閉じていない")


def test_braced_hex_and_named_escapes() -> None:
    assert parse(r"\x{41}", JAVA) == seq(Atom(AtomKind.ESCAPE, r"\x{41}"))
    assert parse(r"\N{DIGIT ONE}", PY) == seq(Atom(AtomKind.ESCAPE, r"\N{DIGIT ONE}"))
    assert parse(r"\x{41", JAVA) == Unreadable(3, "16 進の文字が閉じていない")
    assert parse(r"\N{DIGIT", PY) == Unreadable(3, "文字の名前が閉じていない")


def test_quote() -> None:
    assert parse(r"\Qa.b\E+", JAVA) == seq(
        Quantifier(Quote("a.b"), 1, None, QuantifierMode.GREEDY)
    )
    assert parse(r"\Qa.b", NET) == seq(Quote("a.b"))


def test_leading_flags() -> None:
    assert parse("(?i)a", PY) == seq(LeadingFlags(Flags(frozenset("i"), frozenset())), char("a"))


@pytest.mark.parametrize(
    ("pattern", "dialect", "position"),
    [
        ("(a", PY, 0),
        ("a(b(c)", PY, 1),
        ("a)", PY, 1),
        ("*a", PY, 0),
        ("a|+", PY, 2),
        ("(+a)", PY, 1),
        ("a**", PY, 2),
        ("(?X)", PY, 0),
        ("(?P!a)", PY, 0),
        (r"\Qa\E", PY, 0),
        ("(?<a-b>x)", NET, 3),
        ("[abc", JAVA, 0),
        ("a\\", PY, 1),
        ("(?<n", JAVA, 3),
        ("(?(1)a|b|c)", PY, 0),
        ("(?i)*", PY, 0),
    ],
)
def test_unreadable(pattern: str, dialect: RegexDialect, position: int) -> None:
    result = parse(pattern, dialect)
    assert isinstance(result, Unreadable)
    assert result.position == position
    assert result.reason


def test_balancing_group_reason_names_the_group() -> None:
    result = parse("(?<a-b>x)", NET)
    assert isinstance(result, Unreadable)
    assert "釣り合い" in result.reason


# re._parser は非公開の API で版によって変わりうるので、試験だけで使う。
CORPUS = [
    r"^(a+)+$",
    r"(\w+\s?)+",
    r"(a|ab)*",
    r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})",
    r"(?:/[\w.]+)*",
    r"(-[A-Z]+)*",
    r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})",
    r"hsl\((\d+) (\d+)% (\d+)%\)",
    r"[:/]([^/:\s]+)/([^/\s]+?)(?:\.git)?/?$",
    r"^F[1-9][0-9]*$",
    r"^(#{1,6})\s+\S",
    r"^\s*(```|~~~)",
    r"^( {0,3})(#{1,6})(?=[ \t]|$)",
    r"(?<![\w/.\-@:])(?:\./)?[\w.\-]+(?:/[\w.\-]+)*",
    r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$",
    r"^(.*?)\s*\(#(\d+)\)$",
    r"<(PackageReference|PackageVersion)\b([^>]*?)/?>",
    r"(\w+)\s*=\s*\"([^\"]*)\"",
    r"\.package\s*\(((?:[^()]|\([^()]*\))*)\)",
    r"^FLK-[A-Z]+(-[A-Z]+)*$",
    r"(a)(?(1)b|c)",
    r"(?P<n>x)(?P=n)",
    r"(?:a|b|c|d)+?e*?",
    r"(?>a+)b++",
    r"a{2,}b{,3}c{4}",
    r"(?i:abc)(?=x)(?!y)(?<=z)",
    r"((a)(b(c)))",
    r"(?s:.)+|[^\]]+",
    r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$",
]


def _standard_quantifiers_in(op: object, av: AbcSequence[Any]) -> int:
    """標準の構文解析の一つの節が持つ量指定子の数を、入れ子の中も含めて数える。"""
    own = 0
    nested: list[Any] = []
    if op in (sre_parser.MAX_REPEAT, sre_parser.MIN_REPEAT, sre_parser.POSSESSIVE_REPEAT):
        own = 1
        nested = [av[2]]
    elif op is sre_parser.SUBPATTERN:
        nested = [av[3]]
    elif op is sre_parser.BRANCH:
        nested = list(av[1])
    elif op in (sre_parser.ASSERT, sre_parser.ASSERT_NOT):
        nested = [av[1]]
    elif op is sre_parser.ATOMIC_GROUP:
        nested = [av]
    elif op is sre_parser.GROUPREF_EXISTS:
        nested = [av[1]] if av[2] is None else [av[1], av[2]]
    return own + sum(_standard_quantifiers(items) for items in nested)


def _standard_quantifiers(items: Iterable[tuple[object, AbcSequence[Any]]]) -> int:
    """標準の構文解析の節の並びが持つ量指定子の数を数える。"""
    return sum(_standard_quantifiers_in(op, av) for op, av in items)


def count_standard(pattern: str) -> tuple[int, int]:
    """標準の構文解析の結果から、捕獲の組の数と量指定子の数を数える。"""
    parsed = sre_parser.parse(pattern)
    return parsed.state.groups - 1, _standard_quantifiers(parsed)


def count_tree(node: RegexNode) -> tuple[int, int]:
    match node:
        case Alternation(items) | Sequence(items):
            counts = [count_tree(i) for i in items]
            return sum(c[0] for c in counts), sum(c[1] for c in counts)
        case CaptureGroup(body) | NamedGroup(_, body):
            groups, quantifiers = count_tree(body)
            return groups + 1, quantifiers
        case NonCaptureGroup(body) | AtomicGroup(body) | FlaggedGroup(_, body) | Look(_, _, body):
            return count_tree(body)
        case Conditional(_, yes, no):
            yg, yq = count_tree(yes)
            ng, nq = count_tree(no) if no is not None else (0, 0)
            return yg + ng, yq + nq
        case Quantifier(target):
            groups, quantifiers = count_tree(target)
            return groups, quantifiers + 1
        case _:
            return 0, 0


@pytest.mark.parametrize("pattern", CORPUS)
def test_matches_standard_parser_counts(pattern: str) -> None:
    re.compile(pattern)
    tree = parse(pattern, PY)
    assert not isinstance(tree, Unreadable), tree
    assert count_tree(tree) == count_standard(pattern)


def test_corpus_size() -> None:
    assert len(CORPUS) >= 30


@pytest.mark.parametrize("depth", [3000, 100000])
def test_deeply_nested_groups_are_unreadable_not_a_crash(depth: int) -> None:
    tree = parse("(" * depth + "a" + ")" * depth, PY)
    assert isinstance(tree, Unreadable)
    assert "深すぎる" in tree.reason
