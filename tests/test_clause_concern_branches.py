import pytest

from jigane.findings.discard import _clause_concern


@pytest.mark.parametrize("reason", ["", "。。", "短い。", "範囲外の話である。"])
def test_nothing_is_left_when_no_sentence_states_a_concern(reason: str) -> None:
    assert _clause_concern(reason) is None


def test_a_plain_sentence_is_kept_with_its_full_stop() -> None:
    assert _clause_concern("入力の検査が弱い恐れがある。") == "入力の検査が弱い恐れがある。"


@pytest.mark.parametrize(
    "reason",
    [
        "入力の検査が弱い恐れがある、重ねて出さない。",
        "入力の検査が弱い恐れがあるので蒸し返さない。",
    ],
)
def test_a_trailing_refusal_clause_is_removed(reason: str) -> None:
    assert _clause_concern(reason) == "入力の検査が弱い恐れがある。"


def test_a_refusal_only_sentence_is_skipped_for_the_next_one() -> None:
    assert (
        _clause_concern("範囲外の話である。入力の検査が弱い恐れがある。")
        == "入力の検査が弱い恐れがある。"
    )


def test_a_topic_particle_left_by_the_removal_is_stripped() -> None:
    reason = "ロック取得の順序が崩れる恐れは、宣言で決まっているので蒸し返さない。"
    assert _clause_concern(reason) == "ロック取得の順序が崩れる恐れ。"
    assert _clause_concern(reason, strip_declaration=True) == "ロック取得の順序が崩れる恐れ。"


def test_a_trailing_declaration_clause_is_removed_only_on_request() -> None:
    reason = "入力の検査が弱い恐れがある、宣言で決まっているので蒸し返さない。"
    assert _clause_concern(reason, strip_declaration=True) == "入力の検査が弱い恐れがある。"
    assert _clause_concern(reason) == "入力の検査が弱い恐れがある。"
    assert _clause_concern("宣言で決まっているので蒸し返さない。", strip_declaration=True) == (
        "宣言で決まっている。"
    )


def test_a_declaration_clause_that_is_the_predicate_is_kept() -> None:
    assert (
        _clause_concern("入力の検査は、宣言が決めている、出さない。", strip_declaration=True)
        == "入力の検査は、宣言が決めている。"
    )
    assert (
        _clause_concern("入力の検査は宣言が決めている。", strip_declaration=True)
        == "入力の検査は宣言が決めている。"
    )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("ロックの取得順序は、同じ順序で取るので、出さない。", "ロックの取得順序に懸念がある。"),
        ("ロックの取得順序は、ずれており、出さない。", "ロックの取得順序に懸念がある。"),
        ("ロックの取得順序は、待ちが長く、出さない。", "ロックの取得順序に懸念がある。"),
        ("取得の順序は、で、出さない。", "取得の順序に懸念がある。"),
    ],
)
def test_a_clause_left_unfinished_falls_back_to_the_topic(reason: str, expected: str) -> None:
    assert _clause_concern(reason) == expected


def test_an_unfinished_clause_without_a_topic_moves_on_to_the_next_sentence() -> None:
    assert _clause_concern("ロックの取得が、順序で、出さない。") is None
    assert _clause_concern("検査が弱いのは、で、で、出さない。") is None
    assert (
        _clause_concern("ロックの取得順序が、で、出さない。次の文の検査が弱い恐れがある。")
        == "次の文の検査が弱い恐れがある。"
    )


def test_a_noun_phrase_ending_becomes_a_topic_sentence() -> None:
    assert (
        _clause_concern("ロックの取得順序の置き方。") == "ロックの取得順序の置き方に懸念がある。"
    )


def test_a_sentence_without_a_refusal_is_returned_unchanged_plus_a_full_stop() -> None:
    assert _clause_concern("ロックの取得順序は懸念がある") == "ロックの取得順序は懸念がある。"


def test_a_sentence_cut_off_at_a_comma_without_a_refusal_falls_back_to_the_topic() -> None:
    assert _clause_concern("ロックの取得順序は、取得で、") == "ロックの取得順序に懸念がある。"
    assert _clause_concern("取得の順序は、待ちが長く、") == "取得の順序に懸念がある。"
