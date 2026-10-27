import json
from pathlib import Path
from typing import Any

import pytest
from review_fakes import history_rounds, stored_finding
from shard_prompt_support import shard_prompt

from jigane.findings import discard
from jigane.findings.discard import (
    DiscardedCandidate,
    DiscardKind,
    DisclosedNitReason,
    ExtractedConcern,
    Nit,
    NitReason,
    NoConcern,
    OrdinaryClaim,
    OrdinaryNitReason,
    PassedConcern,
    ShardConcerns,
    discard_claim,
    discard_kind_of,
    nit_of,
    nit_reason_from_json,
    nit_reason_of_claim,
)
from jigane.findings.records import (
    HISTORY_FILE,
    TriagedFinding,
    concerns_section,
    fill_triage,
    load_findings_state,
    passed_concerns,
    read_stored_state,
    save_findings_state,
)
from jigane.groups.definitions import MACRO, MICRO, GroupId
from jigane.llm_runs.finding_model import Finding
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import shards
from jigane.skills.assembly import AssemblyInput
from jigane.skills.macro import discarded_as_nits

UNVERIFIABLE = DiscardKind.UNVERIFIABLE_FROM_DIAGRAM
# 古い結果が主張の末尾に添えていた一文。移行の検査で、古い形を作るために使う
HANDOFF_NOTE = "方針では確かめられない。実装のレビューで確かめる。"


def reason_of(reason: str, kind: DiscardKind) -> NitReason:
    decision = nit_of(reason, discard_claim(reason, kind, None))
    assert isinstance(decision, Nit), decision
    return decision.reason


def concern_of(reason: str, kind: DiscardKind = UNVERIFIABLE) -> str | None:
    match reason_of(reason, kind).concern:
        case ExtractedConcern(text=text):
            return text
        case NoConcern():
            return None


def discarded_claim(reason: str, kind: DiscardKind) -> str:
    return reason_of(reason, kind).claim


# 本物のレビューの runs.json にあった理由の文から、種類ごとに代表を取った
REPRESENTATIVES = [
    (
        (
            "game_dir の綴り違いの正規化は、検証の本体の内部ロジックで、"
            "図に現れていないため問えない。"
        ),
        UNVERIFIABLE,
    ),
    (
        (
            "collect_declaration_background が dict[str, Any] を返す点は、"
            "辞書の中身が図に現れないため、問題かどうか確かめられない。"
        ),
        UNVERIFIABLE,
    ),
    (
        "UI 判定の口は、図から確かめられないが、モジュール内に閉じて手戻りが小さい。",
        DiscardKind.NOT_A_CONTRACT,
    ),
    (
        "_Drawing.shared の set は非公開の描画状態で、境を越えて読まれる型ではない。",
        DiscardKind.NOT_A_CONTRACT,
    ),
    (
        "前の回 F1 の直し方が足した欄の形を問う深掘りに当たるので出さない。",
        DiscardKind.DEEPENING,
    ),
    (
        "前の回 F1 の bot 解析の置き場は図から直ったと読めるので、出さない。",
        DiscardKind.SETTLED_EARLIER,
    ),
    (
        "別の game_dir を選んだことは利用者の判断（意思決定ログ）で、蒸し返さない。",
        DiscardKind.DECIDED_BY_DECLARATION,
    ),
    (
        "MarkupBindingAdapter の置き方は、宣言の「XAML の読み取りは C# の側に置く」と一致する。",
        DiscardKind.DECIDED_BY_DECLARATION,
    ),
    (
        (
            "AddedSignature と DeletedSignature は、old と new を型で分けた和型であり、"
            "同じ知識の写しとは言えない。"
        ),
        DiscardKind.OTHER,
    ),
    ("", DiscardKind.OTHER),
    (
        "ER 図で `declaration` が optional と出るのは、型注記が図に現れていないため問えない。",
        UNVERIFIABLE,
    ),
    (
        "format_row の戻り値の順序は、本体の内容に頼る主張なので捨てる。",
        UNVERIFIABLE,
    ),
    (
        "ログの書式の整理は、契約に現れない内部の話で、指摘にしない。",
        DiscardKind.NOT_A_CONTRACT,
    ),
    (
        "この名前の変更は局所で、変更前から公開の境を越えていないので出さない。",
        DiscardKind.NOT_A_CONTRACT,
    ),
    (
        "前の回 F2 の指摘を重ねて出さない。",
        DiscardKind.DEEPENING,
    ),
    (
        "前の回 F3 で決着済みで、既に直っている。",
        DiscardKind.SETTLED_EARLIER,
    ),
    (
        "この形は決定済みで、蒸し返さない。",
        DiscardKind.DECIDED_BY_DECLARATION,
    ),
    # 図の内容そのものの否定は、図から確かめられない意味ではない
    ("図の上では問題は起きない。", DiscardKind.OTHER),
    ("資料にある関数は、どれも副作用を持たない。", DiscardKind.OTHER),
    (
        "dropped が changed から導ける写しかは、本体を見ないと言えず、図に支えが無い。",
        UNVERIFIABLE,
    ),
    ("値のずれる道筋が図に無く、内容も出ておらず問えない。", UNVERIFIABLE),
]

# 重なる言い回しを含む文。上の行の種類が先に当たる順序を固定する
PRIORITY_CASES = [
    (
        "宣言が決めた形で、前の回に決着済みなので出さない。",
        DiscardKind.SETTLED_EARLIER,
        "SETTLED_EARLIER が DECIDED_BY_DECLARATION より先",
    ),
    (
        "決定済みの判断を、前の回の深掘りで問い直す往復になるので出さない。",
        DiscardKind.DEEPENING,
        "DEEPENING が DECIDED_BY_DECLARATION と SETTLED_EARLIER より先",
    ),
    (
        "前の回に決着済みの論点を再び問う深掘りなので出さない。",
        DiscardKind.DEEPENING,
        "DEEPENING が SETTLED_EARLIER より先",
    ),
    (
        "図には現れないが、局所に閉じて手戻りが小さいので捨てる。",
        DiscardKind.NOT_A_CONTRACT,
        "NOT_A_CONTRACT が UNVERIFIABLE より先",
    ),
    (
        "図からは確かめられないが、契約に現れない内部の話である。",
        DiscardKind.NOT_A_CONTRACT,
        "NOT_A_CONTRACT が UNVERIFIABLE より先（契約の語が後の行でも）",
    ),
]


@pytest.mark.parametrize(("reason", "kind", "why"), PRIORITY_CASES)
def test_earlier_rows_win_when_wordings_overlap(reason: str, kind: DiscardKind, why: str) -> None:
    assert discard_kind_of(reason) is kind, why


@pytest.mark.parametrize(("reason", "kind"), REPRESENTATIVES)
def test_kind_is_read_from_the_wording(reason: str, kind: DiscardKind) -> None:
    assert discard_kind_of(reason) is kind


def test_concern_drops_the_disclaimer_of_an_unverifiable_reason() -> None:
    reason = (
        "Flags の enabled が frozenset[str] である点は、図から確かめられず、読む側も見えない。"
    )
    concern = "Flags の enabled が frozenset[str] である点が懸念である。"
    assert concern_of(reason) == concern
    assert discarded_claim(reason, UNVERIFIABLE) == concern


def test_a_risk_noun_keeps_its_own_ending() -> None:
    reason = "LIST_LIMIT で状態が欠ける恐れは、図からは言えない。"
    assert concern_of(reason) == "LIST_LIMIT で状態が欠ける恐れがある。"


def test_feedback_example_becomes_a_concern() -> None:
    reason = (
        "game_dir の綴り違い（区切り文字、末尾の区切り）の正規化は、"
        "検証の本体の内部ロジックで、図に現れていないため問えない。"
    )
    assert discard_kind_of(reason) is UNVERIFIABLE
    assert discarded_claim(reason, UNVERIFIABLE) == (
        "game_dir の綴り違い（区切り文字、末尾の区切り）の正規化に懸念がある。"
    )


def test_reliance_on_the_body_is_unverifiable_from_the_diagram() -> None:
    reason = (
        "format_row が位置で並べた list[str] を返し、"
        "見出しの table_columns と別に順序を持つ疑いは、"
        "図からは format_row が table_columns を内部で引くかどうか決められず"
        "（シーケンス図の呼び出しが平らで曖昧）、本体の内容に頼る主張になるので捨てる。"
    )
    assert discard_kind_of(reason) is UNVERIFIABLE
    assert discarded_claim(reason, UNVERIFIABLE) == (
        "format_row が位置で並べた list[str] を返し、"
        "見出しの table_columns と別に順序を持つ疑いがある。"
    )


def test_reason_led_by_a_particle_ending_phrase_is_kept_whole() -> None:
    reason = "図では、X の型は、読む側から見えず、図から確かめられない。"
    assert discard_kind_of(reason) is UNVERIFIABLE
    assert concern_of(reason) is None
    assert discarded_claim(reason, UNVERIFIABLE) == reason


def test_reason_is_kept_whole_when_the_disclaimer_cannot_be_cut() -> None:
    reason = "図に現れていないため問えない。"
    assert concern_of(reason) is None
    assert discarded_claim(reason, UNVERIFIABLE) == reason


def test_other_kinds_keep_the_reason() -> None:
    reason = "局所の整理で手戻りが小さい。"
    assert discarded_claim(reason, DiscardKind.NOT_A_CONTRACT) == reason


# 保存された nit が持つ欄。区分は読み込みが補うので持たない
_NIT_BASE: dict[str, Any] = {
    "severity": "nit",
    "viewpoint": "discarded",
    "adjudication": None,
    "theme": None,
}


def _nit(claim: str, kind: DiscardKind, concern: str | None = None) -> TriagedFinding:
    resolved = NoConcern() if concern is None else ExtractedConcern(concern)
    reason = nit_reason_of_claim(claim, OrdinaryClaim(kind, resolved))
    stored = stored_finding(
        {**_NIT_BASE, "id": "F1", "claim": reason.claim, "nit_reason": reason.to_json()}
    )
    return fill_triage(MACRO, stored)


def section_of(findings: list[TriagedFinding]) -> str:
    return concerns_section(passed_concerns(findings))


def _plain(claim: str) -> TriagedFinding:
    return fill_triage(MACRO, stored_finding({"id": "F9", "claim": claim}))


def test_concerns_section_lists_only_unverifiable_ones() -> None:
    section = section_of(
        [
            _nit("A が懸念である。", UNVERIFIABLE, "A が懸念である。"),
            _nit("B は局所である", DiscardKind.NOT_A_CONTRACT),
            _plain("nit_reason を持たない指摘"),
        ]
    )
    lines = section.splitlines()
    assert "### 確かめてほしい懸念" in lines
    assert lines[-1] == "- 方針 F1: A が懸念である。"
    assert section.count("\n- ") == 1


def test_concerns_section_is_empty_without_concerns() -> None:
    assert section_of([_nit("B", DiscardKind.OTHER), _plain("x")]) == ""


def test_micro_prompt_carries_the_section_only_when_macro_has_concerns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    review = create_feature_review(root, ["src"], "p")
    shard = shards.assign_shards(shards.build_units(review), shards.FullTextShard)[0]

    without = shard_prompt(MICRO, AssemblyInput.load(review), shard, 1, 1)
    assert "確かめてほしい懸念" not in without
    assert "\n\n\n## 観点" not in without

    concern = "X が懸念である。"
    _write_findings(review.id.directory(), [_nit(concern, UNVERIFIABLE, concern).to_json()])
    built = shard_prompt(MICRO, AssemblyInput.load(review), shard, 1, 1)
    assert "### 確かめてほしい懸念\n" in built
    assert "\n- 方針 F1: X が懸念である。\n" in built
    assert "from_concern に方針の番号（例 F2）を書く" in built


BARE_TOPIC_REASON = (
    "Slot が Literal の別名か文字列かは図に現れておらず、FigureKind との不ぞろいは確かめられない。"
)


def test_topic_is_taken_up_to_the_first_wa_when_no_comma_follows_it() -> None:
    reason = (
        "Item.order が整数の位置として保存されるかどうかは図から確かめられず、保存の形に頼る。"
    )
    assert concern_of(reason) == "Item.order が整数の位置として保存されるかどうかに懸念がある。"


def test_bare_topic_that_crosses_a_comma_is_not_taken() -> None:
    reason = (
        "Claim.manual が cues と併存し、無意味な組を作れる可能性はあるが、図から確かめられない。"
    )
    assert concern_of(reason) is None


def test_bare_topic_too_short_to_name_a_concern_is_not_taken() -> None:
    assert concern_of("宣言は方針の振る舞いを変えるが、図から確かめられない。") is None


@pytest.mark.parametrize(
    "reason",
    [
        "INTRO の変更は行数だけで、図に契約が崩れる事実が無いので、指摘にしない。",
        "usage の列は保存物で、図から確かめられないが、手戻りが大きい形ではない。",
        "図からは確かめられないが、欠陥には当たらない。",
        "図から確かめられないが、契約の形でもないので捨てる。",
    ],
)
def test_reason_saying_it_is_not_a_defect_is_not_a_concern(reason: str) -> None:
    assert discard_kind_of(reason) is DiscardKind.NOT_A_CONTRACT


def test_a_plain_unverifiable_reason_stays_unverifiable() -> None:
    assert discard_kind_of(BARE_TOPIC_REASON) is UNVERIFIABLE


def test_concerns_section_folds_unspecified_ones_into_one_line() -> None:
    raw = "強制終了で残った置き場の後始末が図に現れないが、運用の話であり、図から確かめられない。"
    section = section_of(
        [
            _nit("A が懸念である。", UNVERIFIABLE, "A が懸念である。"),
            _nit(discarded_claim(raw, UNVERIFIABLE), UNVERIFIABLE),
            _nit(discarded_claim(raw, UNVERIFIABLE), UNVERIFIABLE),
        ]
    )
    assert section.splitlines()[-2:] == [
        "- 方針 F1: A が懸念である。",
        "- 方針では確かめていない懸念が 2 件ある（主題を取り出せない）。",
    ]
    assert raw not in section


def test_concerns_section_with_only_unspecified_ones_keeps_the_section() -> None:
    raw = "図に現れていないため問えない。"
    section = section_of([_nit(discarded_claim(raw, UNVERIFIABLE), UNVERIFIABLE)])
    assert "### 確かめてほしい懸念" in section
    assert (
        section.splitlines()[-1]
        == "- 方針では確かめていない懸念が 1 件ある（主題を取り出せない）。"
    )


def test_unspecified_one_stays_whole_in_the_nit() -> None:
    raw = "図に現れていないため問えない。"
    assert discarded_claim(raw, UNVERIFIABLE) == raw


def test_intent_and_composition_are_not_the_diagram() -> None:
    # 「意図」「構図」の図は、図の断りではない
    for reason in ("意図した形かどうかは確かめていない。", "構図が分からない。"):
        assert discard_kind_of(reason) is not DiscardKind.UNVERIFIABLE_FROM_DIAGRAM


def test_mismatch_alone_is_not_a_small_contract() -> None:
    # 「と合わない」だけでは契約として小さいとは言えず、図の断りを契約の側へ寄せない
    reason = "宣言と合わないかどうかは、図からは確かめられない。"
    assert discard_kind_of(reason) is DiscardKind.UNVERIFIABLE_FROM_DIAGRAM


def test_reason_ending_like_a_concern_is_still_counted_when_it_has_no_concern_field() -> None:
    # 元の文がたまたま「がある。」で終わっても、concern の欄が無ければ並べない
    raw = "図に現れていないため問えないが、別の確認がある。"
    assert concern_of(raw) is None
    section = section_of([_nit(discarded_claim(raw, UNVERIFIABLE), UNVERIFIABLE)])
    assert section.splitlines()[-1] == (
        "- 方針では確かめていない懸念が 1 件ある（主題を取り出せない）。"
    )
    assert raw not in section


def test_discarded_as_nits_stores_concern_only_when_extracted(tmp_path: Path) -> None:
    taken = "LIST_LIMIT で状態が欠ける恐れは、図からは言えない。"
    kept = "図に現れていないため問えない。"
    other = "局所の整理で手戻りが小さい。"
    nits = discarded_as_nits(
        [
            DiscardedCandidate("S1", taken),
            DiscardedCandidate("S2", kept),
            DiscardedCandidate("S3", other),
        ]
    )
    assert nits[0]["nit_reason"]["concern"] == "LIST_LIMIT で状態が欠ける恐れがある。"
    assert nits[1]["nit_reason"]["concern"] is None
    # 述べているだけで懸念の語を含まない文は、取り出せなかったものとして元の文を主張にする
    assert nits[2]["nit_reason"]["concern"] is None
    assert nits[2]["claim"] == other
    # 保存して読み戻しても欄が残る
    path = tmp_path / "findings.json"
    path.write_text(json.dumps({"groups": {"macro": nits}}), encoding="utf-8")
    back = json.loads(path.read_text(encoding="utf-8"))["groups"]["macro"]
    # 採番前の nit は id を持たないので、読み戻しのために振る
    typed = [
        fill_triage(MACRO, stored_finding({**f, "id": f"F{n}"})) for n, f in enumerate(back, 1)
    ]
    assert section_of(typed).splitlines()[-2:] == [
        "- 方針 F1: LIST_LIMIT で状態が欠ける恐れがある。",
        "- 方針では確かめていない懸念が 1 件ある（主題を取り出せない）。",
    ]


CLAUSE_CASES = [
    (
        DiscardKind.NOT_A_CONTRACT,
        "テストの網羅の穴は、実装レビューの受け持ちで方針の指摘にならない。",
        "テストの網羅の穴に懸念がある。",
    ),
]


@pytest.mark.parametrize(("kind", "reason", "concern"), CLAUSE_CASES)
def test_concern_is_taken_from_every_kind(kind: DiscardKind, reason: str, concern: str) -> None:
    assert concern_of(reason, kind) == concern
    assert discarded_claim(reason, kind) == concern


UNEXTRACTABLE_CASES = [
    (
        DiscardKind.DEEPENING,
        "前の回 F1 の直し方への深掘りに当たる。図から直ったと読めるので、出さない。",
    ),
    (
        DiscardKind.DECIDED_BY_DECLARATION,
        "Interval が文字列で持つ形は、宣言が決めた判断である。覆す事実は読めない。",
    ),
    (DiscardKind.OTHER, "命名の揃えの話に収まる。なので指摘にしない"),
]


@pytest.mark.parametrize(("kind", "reason"), UNEXTRACTABLE_CASES)
def test_sentence_without_a_concern_word_is_not_extracted(kind: DiscardKind, reason: str) -> None:
    assert concern_of(reason, kind) is None
    assert discarded_claim(reason, kind) == reason


def test_refusal_only_reason_has_no_concern() -> None:
    reason = "なので出さない。"
    assert concern_of(reason, DiscardKind.OTHER) is None
    assert discarded_claim(reason, DiscardKind.OTHER) == reason


def test_nit_reason_keeps_kind_text_and_concern_and_derives_handoff() -> None:
    reason = reason_of("Foo の保存形式は、図からは言えない。", UNVERIFIABLE)
    assert reason == OrdinaryNitReason(
        UNVERIFIABLE,
        "Foo の保存形式は、図からは言えない。",
        ExtractedConcern("Foo の保存形式に懸念がある。"),
    )
    assert reason.sent_to_micro is True
    assert reason_of("理由", DiscardKind.DEEPENING).sent_to_micro is False
    bare = reason_of("なので出さない。", DiscardKind.OTHER)
    assert bare.concern == NoConcern()
    assert bare.claim == "なので出さない。"


def test_nit_reason_round_trips_through_json() -> None:
    for reason in (
        reason_of("図に現れていないため問えない。", UNVERIFIABLE),
        reason_of("A が懸念である。なので出さない。", DiscardKind.OTHER),
    ):
        assert nit_reason_from_json(reason.to_json()) == reason
    assert reason_of("なので出さない。", DiscardKind.OTHER).to_json()["concern"] is None


def test_nit_reason_reads_only_the_current_form() -> None:
    assert _plain("x").finding.nit_reason is None
    # 捨てた候補でない指摘の古い欄は、read_findings_json の移しの対象外で読み替えない
    legacy = stored_finding({"id": "F1", "claim": "A", "discard_kind": "other"})
    assert legacy.body.nit_reason is None


def _write_findings(directory: Path, findings: list[dict[str, Any]]) -> None:
    raw = {"last_number": 0, "groups": {"macro": findings}}
    (directory / "findings.json").write_text(json.dumps(raw), encoding="utf-8")


def test_load_findings_state_migrates_the_legacy_nit_form(tmp_path: Path) -> None:
    legacy_with_concern = {
        **_NIT_BASE,
        "id": "F1",
        "claim": "A が懸念である。" + HANDOFF_NOTE,
        "discard_kind": UNVERIFIABLE.value,
        "concern": "A が懸念である。",
    }
    legacy_without_concern = {
        **_NIT_BASE,
        "id": "F2",
        "claim": "局所の整理で手戻りが小さい。",
        "discard_kind": DiscardKind.NOT_A_CONTRACT.value,
    }
    oldest = {**_NIT_BASE, "id": "F3", "claim": "種類の無い。" + HANDOFF_NOTE}
    _write_findings(tmp_path, [legacy_with_concern, legacy_without_concern, oldest])
    loaded = {
        f.finding.id: f.finding for f in load_findings_state(tmp_path).groups[GroupId("macro")]
    }

    first = loaded["F1"]
    assert "discard_kind" not in first.extra
    assert "concern" not in first.extra
    reason = first.nit_reason
    concern = ExtractedConcern("A が懸念である。")
    assert reason == OrdinaryNitReason(UNVERIFIABLE, "A が懸念である。", concern)
    assert first.claim == reason.claim == "A が懸念である。"
    second = loaded["F2"].nit_reason
    expected = OrdinaryNitReason(
        DiscardKind.NOT_A_CONTRACT, "局所の整理で手戻りが小さい。", NoConcern()
    )
    assert second == expected
    assert loaded["F2"].claim == "局所の整理で手戻りが小さい。"
    # 種類を持たない最も古い形は、添え文を外すだけで nit_reason を作らない
    assert loaded["F3"].nit_reason is None
    assert loaded["F3"].claim == "種類の無い。"


def test_legacy_nit_is_read_by_the_rule_of_its_version_not_the_current_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = discard.DISCARD_KIND_FACTS
    monkeypatch.setitem(
        facts, DiscardKind.SETTLED_EARLIER, discard.KindFacts("前の回で決着", becomes_nit=True)
    )
    settled = {**_NIT_BASE, "id": "F2", "claim": "B。", "discard_kind": "settled_earlier"}
    stored = {**_NIT_BASE, "id": "F3", "claim": "C。"}
    stored["nit_reason"] = _nit("C。", UNVERIFIABLE, "C。").to_json()
    stored["nit_reason"]["kind"] = "settled_earlier"
    _write_findings(tmp_path, [settled, stored])
    loaded = {
        f.finding.id: f.finding for f in load_findings_state(tmp_path).groups[GroupId("macro")]
    }
    assert loaded["F2"].nit_reason is None
    assert loaded["F3"].nit_reason is None


def test_migration_is_idempotent_on_the_current_form(tmp_path: Path) -> None:
    current = _nit("A が懸念である。", UNVERIFIABLE, "A が懸念である。").to_json()
    _write_findings(tmp_path, [current])
    loaded = load_findings_state(tmp_path).groups[GroupId("macro")][0].finding
    assert loaded.claim == current["claim"]
    assert loaded.nit_reason is not None
    assert loaded.nit_reason.to_json() == current["nit_reason"]


def test_finding_schema_has_no_legacy_nit_fields() -> None:
    properties = Finding.model_fields
    assert "nit_reason" in properties
    assert "discard_kind" not in properties
    assert "concern" not in properties


def test_saving_the_state_drops_the_legacy_nit_fields(tmp_path: Path) -> None:
    legacy = {
        **_NIT_BASE,
        "id": "F1",
        "claim": "A が懸念である。" + HANDOFF_NOTE,
        "discard_kind": UNVERIFIABLE.value,
        "concern": "A が懸念である。",
    }
    _write_findings(tmp_path, [legacy])
    # 読み直した保存物をそのまま書き戻す書き手の道
    save_findings_state(tmp_path, read_stored_state(tmp_path))
    stored = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    saved = stored["groups"]["macro"][0]
    assert "discard_kind" not in saved
    assert "concern" not in saved
    expected = OrdinaryNitReason(
        UNVERIFIABLE, "A が懸念である。", ExtractedConcern("A が懸念である。")
    )
    assert stored_finding(saved).body.nit_reason == expected


def test_history_read_migrates_the_legacy_nit_form(tmp_path: Path) -> None:
    legacy = {
        **_NIT_BASE,
        "id": "F1",
        "claim": "A が懸念である。" + HANDOFF_NOTE,
        "discard_kind": UNVERIFIABLE.value,
        "concern": "A が懸念である。",
    }
    raw = {"groups": {"macro": [{"ran_at": None, "findings": [legacy]}]}}
    (tmp_path / HISTORY_FILE).write_text(json.dumps(raw), encoding="utf-8")
    [round_] = history_rounds(tmp_path, "macro")  # type: ignore[arg-type]
    [stored] = round_.findings
    assert "discard_kind" not in stored.to_json()
    expected = OrdinaryNitReason(
        UNVERIFIABLE, "A が懸念である。", ExtractedConcern("A が懸念である。")
    )
    assert stored.body.nit_reason == expected


def test_history_read_keeps_a_legacy_nit_of_an_unknown_kind_and_other_findings(
    tmp_path: Path,
) -> None:
    unknown = {**_NIT_BASE, "id": "F1", "claim": "A。" + HANDOFF_NOTE, "discard_kind": "gone"}
    # 捨てた候補でない指摘は、古い欄の名前を持っていても触らない
    other = {
        "id": "F2",
        "severity": "warning",
        "viewpoint": "V",
        "claim": "B。" + HANDOFF_NOTE,
        "discard_kind": "gone",
    }
    raw = {"groups": {"macro": [{"ran_at": None, "findings": [unknown, other]}]}}
    (tmp_path / HISTORY_FILE).write_text(json.dumps(raw), encoding="utf-8")
    [round_] = history_rounds(tmp_path, "macro")  # type: ignore[arg-type]
    first, second = round_.findings
    assert first.body.claim == "A。"
    assert "discard_kind" not in first.to_json()
    assert first.body.nit_reason is None
    assert second.to_json()["discard_kind"] == "gone"


def test_passed_concerns_selects_once_and_counts_unspecified() -> None:
    raw = "図に現れていないため問えない。"
    concerns = passed_concerns(
        [
            _nit("A が懸念である。", UNVERIFIABLE, "A が懸念である。"),
            _nit(discarded_claim(raw, UNVERIFIABLE), UNVERIFIABLE),
            _nit("B は局所である", DiscardKind.NOT_A_CONTRACT),
            _plain("nit_reason を持たない指摘"),
        ]
    )
    assert concerns.passed == (PassedConcern("F1", "A が懸念である。"),)
    assert concerns.unspecified == 1
    assert concerns_section(concerns).count("\n- ") == 2


def test_section_and_record_read_the_same_tuple() -> None:
    concerns = ShardConcerns((PassedConcern("F7", "X が懸念である。"),), 0)
    assert "- 方針 F7: X が懸念である。" in concerns_section(concerns)
    assert concerns_section(ShardConcerns()) == ""


def test_places_belong_to_disclosed_limitation_only() -> None:
    concern = ExtractedConcern("c")
    disclosed = DisclosedNitReason("t", concern, ("a", "b"))
    assert disclosed.kind is DiscardKind.DISCLOSED_LIMITATION
    assert disclosed.to_json() == {
        "kind": "disclosed_limitation",
        "text": "t",
        "concern": "c",
        "places": ["a", "b"],
    }
    assert nit_reason_from_json(disclosed.to_json()) == disclosed
    ordinary = OrdinaryNitReason(DiscardKind.OTHER, "t", concern)
    assert ordinary.to_json()["places"] == []
    assert nit_reason_from_json(ordinary.to_json()) == ordinary
    # 方針の群が捨てた開示済みの限界は場所を述べない
    bare = DisclosedNitReason("t", concern)
    assert nit_reason_from_json(bare.to_json()) == bare
    assert bare.places == ()


def test_ordinary_reason_rejects_stored_places_and_disclosed_kind() -> None:
    raw = OrdinaryNitReason(DiscardKind.OTHER, "t", ExtractedConcern("c")).to_json()
    with pytest.raises(ValueError, match="開示の場所"):
        nit_reason_from_json({**raw, "places": ["a"]})
    with pytest.raises(ValueError, match="DisclosedNitReason"):
        OrdinaryNitReason(DiscardKind.DISCLOSED_LIMITATION, "t", ExtractedConcern("c"))
