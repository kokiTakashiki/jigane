# ruff: noqa: E501
# 本物の文をそのまま表にするので、行の長さの上限は掛けない
import pytest

from jigane.findings.discard import (
    DiscardedCandidate,
    DiscardKind,
    ExtractedConcern,
    Nit,
    NitReason,
    NoConcern,
    NotNit,
    NotNitKind,
    OrdinaryNitReason,
    discard_claim,
    discard_kind_of,
    is_concern_sentence,
    nit_of,
)
from jigane.skills.macro import discarded_as_nits

# 本物の置き場の findings.json（捨てた候補から写した nit）にあった理由の文と、期待する懸念の文。
# 懸念の文は、元の文の対象と問題の両方を含むこと
UNVERIFIABLE_TABLE = [
    (
        "server.py の終了コード 1 と 3 の扱いが数の直書きかどうかは、図に定数や分岐の原文が無く、図から確かめられないため捨てた。",
        "server.py の終了コード 1 と 3 の扱いが数の直書きかどうかに懸念がある。",
    ),
    (
        "runs.js が失敗の帯を未実行の群にも出す変更は、読む型や鍵が図に現れず、契約の形を言えないため捨てた。",
        "runs.js が失敗の帯を未実行の群にも出す変更に懸念がある。",
    ),
    (
        "FailureView.to_json に text（文の形）が入る点は表示の文言の話で、契約の綻びとして図から確かめられないので捨てた。",
        "FailureView.to_json に text（文の形）が入る点が懸念である。",
    ),
    (
        "ViewpointId に M5 が無い点は、観点の識別子の他の定義（出力の形式など）が図に無く、写しの食い違いを図から確かめられない。",
        "ViewpointId に M5 が無い点が懸念である。",
    ),
    (
        "処理失敗の終了コードの値は、図にも表にも現れず、外形から確かめられない。",
        "処理失敗の終了コードの値に懸念がある。",
    ),
    (
        (
            "マクロの履歴行は区分と deepens を載せ、ミクロの履歴行は deepens を無視する、"
            "という非対称の知識がテストと雛形の文面に分かれて置かれている。"
            "ただし、その写しが契約になるかは図からは確かめられない。内部ロジックとテストの話なので出さない。"
        ),
        (
            "マクロの履歴行は区分と deepens を載せ、ミクロの履歴行は deepens を無視する、"
            "という非対称の知識がテストと雛形の文面に分かれて置かれている点に懸念がある。"
        ),
    ),
    (
        "with_limit を macro の側で呼ぶ置き場所が妥当かは、他の呼び出し元が claude.run の失敗をどう扱うかに依り、図から確かめられない。",
        "with_limit を macro の側で呼ぶ置き場所が妥当かに懸念がある。",
    ),
    (
        "表示側（runs.js）が failures の鍵や出所の文言を別に綴っているという懸念は、JS の中身が図に現れず、根拠の位置を示せない。",
        "表示側（runs.js）が failures の鍵や出所の文言を別に綴っているという懸念がある。",
    ),
    (
        "ViewpointId の他に観点の値を持つ出力の型があるかは、図にその型が現れず、図から確かめられない。",
        "ViewpointId の他に観点の値を持つ出力の型があるかに懸念がある。",
    ),
    (
        (
            "PolicyRow に heading_words が無く、方針の群が既定の群になるという判別の持ち方は、"
            "heading_group の本体の分岐であり、図からは判別が二か所以上にあるとは言えない。"
        ),
        "PolicyRow に heading_words が無く、方針の群が既定の群になるという判別の持ち方に懸念がある。",
    ),
]


def _nit_reason(reason: str) -> NitReason:
    decision = nit_of(reason, discard_claim(reason, None, None))
    assert isinstance(decision, Nit), decision
    return decision.reason


@pytest.mark.parametrize(("reason", "concern"), UNVERIFIABLE_TABLE)
def test_unverifiable_reason_keeps_subject_and_predicate(reason: str, concern: str) -> None:
    assert discard_kind_of(reason) is DiscardKind.UNVERIFIABLE_FROM_DIAGRAM
    assert _nit_reason(reason).concern == ExtractedConcern(concern)


DECLARATION_TABLE = [
    (
        (
            "clone URL が生成器の定数と定義側の Facts に二重に写されているという候補は、"
            "Facts にあるのは uv_install_url だけで、clone URL の写しを図から示せず、一か所に持つことは宣言が決めている。"
        ),
        "clone URL が生成器の定数と定義側の Facts に二重に写されているという候補に懸念がある。",
    ),
    (
        "ログの保存先を二か所に分ける形は、宣言で決まっているので蒸し返さない。",
        "ログの保存先を二か所に分ける形に懸念がある。",
    ),
    (
        "出力の欄を足す案は、前に決めた形を変えるため蒸し返さない。",
        "出力の欄を足す案に懸念がある。",
    ),
    (
        "鍵の綴りを JS 側で持つ点は、決定済みの構成であり、蒸し返さない。",
        "鍵の綴りを JS 側で持つ点が懸念である。",
    ),
]


@pytest.mark.parametrize(("reason", "concern"), DECLARATION_TABLE)
def test_declaration_reason_claim_matches_the_table_exactly(reason: str, concern: str) -> None:
    assert discard_kind_of(reason) is DiscardKind.DECIDED_BY_DECLARATION
    assert _nit_reason(reason).claim == concern


def test_settled_earlier_is_not_a_nit() -> None:
    reason = "前の回 F1（区別と出力の形を名前の付いた型で一か所に持つ）は、GateKind と Finding の追加で直った。"
    assert discard_kind_of(reason) is DiscardKind.SETTLED_EARLIER
    assert nit_of(reason, discard_claim(reason, None, None)) == NotNit(NotNitKind.SETTLED_EARLIER)
    discarded = [
        DiscardedCandidate("S1", reason),
        DiscardedCandidate("S2", "X の型は、図から確かめられない。"),
    ]
    nits = discarded_as_nits(discarded)
    assert [n["claim"] for n in nits] == ["X の型に懸念がある。"]


def test_empty_reason_is_not_a_nit() -> None:
    assert nit_of("", discard_claim("", None, None)) == NotNit(NotNitKind.EMPTY_CLAIM)
    assert discarded_as_nits([DiscardedCandidate("S1", "  ")]) == []


def test_empty_concern_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="空"):
        ExtractedConcern(" ")


def test_settled_earlier_cannot_be_a_nit_reason() -> None:
    with pytest.raises(ValueError, match="SETTLED_EARLIER"):
        OrdinaryNitReason(DiscardKind.SETTLED_EARLIER, "直った。", NoConcern())


@pytest.mark.parametrize(("reason", "concern"), UNVERIFIABLE_TABLE + DECLARATION_TABLE)
def test_every_table_output_is_a_complete_concern_sentence(reason: str, concern: str) -> None:
    claim = _nit_reason(reason).claim
    assert claim == concern
    assert "懸念" in claim
    assert is_concern_sentence(claim)


@pytest.mark.parametrize(
    "text",
    ["ログの保存先を二か所に分ける形。", "決定済みの構成であり。", "X は、懸念があり。", ""],
)
def test_non_concern_sentences_are_rejected(text: str) -> None:
    assert not is_concern_sentence(text)


def test_unextractable_sentence_falls_back_to_the_original() -> None:
    reason = "出力の形は決まっているので蒸し返さない。"
    decision = nit_of(reason, discard_claim(reason, None, None))
    assert isinstance(decision, Nit)
    if isinstance(decision.reason.concern, NoConcern):
        assert decision.reason.claim == reason
    else:
        assert is_concern_sentence(decision.reason.claim)
