import pytest

from jigane.repository_docs.skip_reason import (
    HasNul,
    LinkInBase,
    Missing,
    NotAbsolute,
    NotInBase,
    NotRegularFile,
    NotUtf8,
    SecretName,
    SkipReason,
    TooLarge,
    Unreadable,
    describe,
    record_word,
)

CASES: list[tuple[SkipReason, str, str]] = [
    (NotAbsolute(), "絶対パスではありません", "not_absolute"),
    (SecretName(), "秘密のファイル名に当たります", "secret_name"),
    (Missing(), "ファイルがありません", "missing"),
    (NotInBase(), "レビューの起点にファイルがありません", "not_in_base"),
    (LinkInBase(), "レビューの起点でシンボリックリンクです", "link_in_base"),
    (NotRegularFile(), "通常のファイルではありません", "not_regular_file"),
    (TooLarge(40100), "本文が 32768 バイトを超えています（40100 バイト）", "too_large"),
    (NotUtf8(), "UTF-8 として読めません", "not_utf8"),
    (HasNul(), "NUL を含みます", "has_nul"),
    (Unreadable("Permission denied"), "読めません（Permission denied）", "unreadable"),
]


@pytest.mark.parametrize(("reason", "sentence", "word"), CASES)
def test_each_skip_reason_has_sentence_and_record_word(
    reason: SkipReason, sentence: str, word: str
) -> None:
    assert describe(reason) == sentence
    assert record_word(reason) == word
