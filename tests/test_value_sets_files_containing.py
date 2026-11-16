"""`_files_containing` が、phrase ごとの素朴な正規表現検索と同じ結果を返すことの試験。"""

import random
import re
from collections.abc import Mapping

import pytest

from jigane.policy_materials.value_sets import _files_containing

WORD = re.compile(r"[\w-]+")


def _reference(values: set[str], texts: Mapping[str, str]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {value: set() for value in values}
    for path, text in texts.items():
        words = set(WORD.findall(text))
        for value in values:
            if WORD.fullmatch(value):
                if value in words:
                    found[value].add(path)
            elif re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text):
                found[value].add(path)
    return found


CASES: dict[str, tuple[set[str], dict[str, str]]] = {
    "overlap": ({"ab cd", "cd ef"}, {"t": "ab cd ef"}),
    "prefix": (
        {"foo bar", "foo bar baz"},
        {"a": "foo bar baz", "b": "foo bar", "c": "foo bar-x", "d": "foo barx"},
    ),
    "prefix_chain": (
        {"a b", "a b c", "a b c d"},
        {"a": "a b c d", "b": "a b cd", "c": "a b c"},
    ),
    "symbols": (
        {"a.b", "(x)", "-y-", "foo."},
        {
            "1": "a.b",
            "2": "(x)",
            "3": "-y-",
            "4": "foo.",
            "5": "xa.b",
            "6": "a.bx",
            "7": " (x) -y- foo. ",
            "8": "a(x)b",
            "9": "foo.bar",
        },
    ),
    "start_end": (
        {"foo bar"},
        {
            "s": "foo bar tail",
            "e": "head foo bar",
            "w": "foo bar",
            "n": "xfoo bar",
            "m": "foo barx",
        },
    ),
    "unicode": (
        {"日本 語", "é a", "a_ b", "1 2", "語 é"},
        {
            "1": "日本 語",
            "2": "x日本 語",
            "3": "日本 語x",
            "4": "é a",
            "5": "aé a",
            "6": "é ab",
            "7": "a_ b",
            "8": "_a_ b",
            "9": "1 2",
            "10": "01 2",
            "11": "1 23",
            "12": "語 é",
            "13": "語 éé",
        },
    ),
    "boundary_fail": ({"foo bar"}, {"a": "xfoo bar", "b": "foo barx", "c": "xfoo barx"}),
    "empty_texts": ({"a", "a b", "(x)"}, {}),
    "empty_text_body": ({"a", "a b"}, {"p": ""}),
    "regex_special": (
        {"a|b", "a.*", "[x]", "\\d c", "a+ b", "^c $", "(?:x) y"},
        {
            "1": "a|b a.* [x] \\d c a+ b ^c $ (?:x) y",
            "2": "aab c",
            "3": "a b",
            "4": "xa|b",
        },
    ),
    "nonword_only": (
        {"->", "[", "'*'", "--", " "},
        {
            "1": "a -> b",
            "2": "x[0]",
            "3": "'*' y",
            "4": "-->",
            "5": "a-->b",
            "6": "--",
            "7": "a b",
            "8": "'*'x",
        },
    ),
    "dash_boundary": (
        {"->", "1.0", "# no", "no-x", "-1.0"},
        {
            "1": "a --> b",
            "2": "x -1.0 y",
            "3": "# no-x",
            "4": "no-x",
            "5": "1.0",
            "6": "a1.0",
            "7": "x-1.0",
            "8": "# no x",
        },
    ),
    "words_mixed": (
        {"foo", "foo-bar", "foo bar", "x"},
        {"1": "foo bar", "2": "foo-bar", "3": "foo_bar", "4": "x"},
    ),
}


@pytest.mark.parametrize("name", CASES)
def test_fixed_cases_match_reference(name: str) -> None:
    values, texts = CASES[name]
    assert _files_containing(set(values), texts) == _reference(values, texts)


def test_empty_values_return_empty_mapping() -> None:
    assert _files_containing(set(), {"p": "abc"}) == {}


ALPHABET = ["a", "b", " ", ".", "-", "(", ")", "_", "語", "é", "1", ">"]


def _random_string(rng: random.Random, low: int, high: int) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(low, high)))


def _random_input(rng: random.Random) -> tuple[set[str], dict[str, str]]:
    texts = {f"f{i}": _random_string(rng, 0, 14) for i in range(rng.randint(0, 4))}
    values: set[str] = set()
    for _ in range(rng.randint(1, 6)):
        if texts and rng.random() < 0.7:
            # 本文の断片から切り出すと、当たりが出やすい
            text = rng.choice(list(texts.values()))
            if text:
                start = rng.randrange(len(text))
                values.add(text[start : rng.randint(start + 1, len(text))])
                continue
        values.add(_random_string(rng, 1, 5))
    return values, texts


def test_random_small_inputs_match_reference() -> None:
    rng = random.Random(20261112)  # noqa: S311 -- seed を固定した試験の入力で、暗号用途ではない
    for _ in range(6000):
        values, texts = _random_input(rng)
        assert _files_containing(set(values), texts) == _reference(values, texts), (values, texts)
