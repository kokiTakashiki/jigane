import json
from collections.abc import Callable

import pytest

from jigane.text import json_input
from jigane.text.json_input import JsonTooDeepError, loads_bounded, reject_deep_json

LIMIT = 5
FORMS: list[Callable[[str], str | bytes]] = [str, str.encode]


@pytest.mark.parametrize("form", FORMS, ids=["str", "bytes"])
def test_nesting_at_the_limit_is_read(form: Callable[[str], str | bytes]) -> None:
    text = "[" * LIMIT + "]" * LIMIT
    document = form(text)

    assert loads_bounded(document, LIMIT) == json.loads(text)


@pytest.mark.parametrize("form", FORMS, ids=["str", "bytes"])
def test_nesting_over_the_limit_is_rejected(form: Callable[[str], str | bytes]) -> None:
    text = '{"a": ' * (LIMIT + 1) + "1" + "}" * (LIMIT + 1)
    document = form(text)

    with pytest.raises(JsonTooDeepError) as raised:
        loads_bounded(document, LIMIT)
    assert raised.value.limit == LIMIT


@pytest.mark.parametrize(
    "value",
    [
        "[[[[[[[[",
        'say \\"[[[[[[[[\\" here',
        "[[[[[[ \\\\ ]]]]]]",
    ],
    ids=["brackets", "escaped-quote", "escaped-backslash"],
)
def test_brackets_inside_strings_are_not_counted(value: str) -> None:
    text = json.dumps({"s": value})

    reject_deep_json(text, 1)


def test_an_escaped_backslash_closes_the_string_before_outer_brackets() -> None:
    # "x\\" の後の引用符は字句の閉じ。続く括弧は文字列の外で、数える
    text = '["x\\\\", ' + "[" * LIMIT + "]" * LIMIT + "]"

    with pytest.raises(JsonTooDeepError):
        reject_deep_json(text, LIMIT)


def test_recursion_error_from_json_is_reported_as_too_deep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def overflowing(_document: object, **_options: object) -> object:
        raise RecursionError

    monkeypatch.setattr(json, "loads", overflowing)

    with pytest.raises(JsonTooDeepError):
        loads_bounded("[]", LIMIT)


@pytest.mark.parametrize(
    "document",
    ['"' + '\\"' * 50, '"' + "a" * 50, '"' + '\\"' * 50 + "\\"],
    ids=["escaped-quotes", "plain", "trailing-backslash"],
)
def test_an_unclosed_string_is_one_token_to_the_end(document: str) -> None:
    # 照合が途中で失敗すると、後の引用符から読み直して長さの二乗の時間がかかる。
    # 閉じない字句は一つの字句として終わりまで読むので、読み直しが起きない
    tokens = [token.span() for token in json_input._STRING_OR_BRACKET_TEXT.finditer(document)]
    assert tokens == [(0, len(document))]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "utf-8-sig"])
def test_bytes_are_counted_as_json_loads_decodes_them(encoding: str) -> None:
    # 生の bytes を数えると、UTF-16 と UTF-32 の括弧を見落とす。読む側と同じ文字列を数える
    deep = ("[" * (LIMIT + 1) + "]" * (LIMIT + 1)).encode(encoding)
    shallow = ("[" * LIMIT + "]" * LIMIT).encode(encoding)

    with pytest.raises(JsonTooDeepError):
        loads_bounded(deep, LIMIT)
    assert loads_bounded(shallow, LIMIT) == json.loads(shallow)


def test_strict_false_reads_a_raw_newline_inside_a_string() -> None:
    document = '{"a": "x\ny"}'

    assert loads_bounded(document, LIMIT, strict=False) == {"a": "x\ny"}
    with pytest.raises(json.JSONDecodeError):
        loads_bounded(document, LIMIT)
