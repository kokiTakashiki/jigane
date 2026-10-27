"""方針の群の出力にある、捨てた種類と懸念の文の明示の項目。"""

from pathlib import Path
from typing import Any, NoReturn

import jsonschema
import pytest
from fakes import MacroFake, discovered, settings_for

from jigane.findings import discard
from jigane.findings.discard import DiscardedCandidate, DiscardKind
from jigane.findings.records import triaged_dicts
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput

EXPLICIT = {
    "source": "S1",
    "reason": "図に現れていない呼び出しに頼る",
    "kind": "unverifiable_from_diagram",
    "concern": "X の再試行が二重に走る恐れがある。",
}


def _forbid_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom_kind(_reason: str) -> NoReturn:
        raise AssertionError("正規表現の読み戻しが呼ばれた")

    def boom_concern(_reason: str, _kind: object) -> NoReturn:
        raise AssertionError("正規表現の読み戻しが呼ばれた")

    monkeypatch.setattr(discard, "discard_kind_of", boom_kind)
    monkeypatch.setattr(discard, "_concern_of", boom_concern)


def _nit_reasons(project: Path, discarded: list[dict[str, str]]) -> list[dict[str, Any]]:
    review = create_feature_review(project, ["src"], "p")
    output = {
        "findings": [{**discovered("M2"), "in_scope": True, "scope_reason": ""}],
        "merged": [],
        "discarded": discarded,
    }
    result = macro.run_macro(
        AssemblyInput.load(review), settings_for(MacroFake([], output=output), max_budget_usd=0.5)
    )
    return [
        f["nit_reason"] for f in triaged_dicts(result.findings) if f["viewpoint"] == "discarded"
    ]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_explicit_fields_go_to_nit_reason_without_readback(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_readback(monkeypatch)
    assert _nit_reasons(project, [EXPLICIT]) == [
        {
            "kind": "unverifiable_from_diagram",
            "text": EXPLICIT["reason"],
            "concern": EXPLICIT["concern"],
            "places": [],
        }
    ]


def test_explicit_concern_that_is_not_a_concern_sentence_is_no_concern(project: Path) -> None:
    broken = {**EXPLICIT, "concern": "X の再試行が二重に走り、"}
    (reason,) = _nit_reasons(project, [broken])
    assert reason["concern"] is None
    assert reason["kind"] == "unverifiable_from_diagram"


def test_explicit_settled_earlier_is_not_a_nit(project: Path) -> None:
    settled = {"source": "S1", "reason": "r", "kind": "settled_earlier", "concern": "懸念の恐れ。"}
    assert _nit_reasons(project, [settled]) == []


def test_output_without_the_fields_is_read_back_from_the_sentence(project: Path) -> None:
    sentence = "X の型は、図から確かめられない。"
    (reason,) = _nit_reasons(project, [{"source": "S1", "reason": sentence}])
    assert reason["kind"] == "unverifiable_from_diagram"
    assert reason["concern"] == "X の型に懸念がある。"


def test_kind_only_reads_the_concern_back_from_the_sentence() -> None:
    reason = "X の型は、図から確かめられない。"
    decision = discard.nit_of(
        reason, discard.discard_claim(reason, DiscardKind.UNVERIFIABLE_FROM_DIAGRAM, None)
    )
    assert isinstance(decision, discard.Nit)
    assert decision.reason.claim == "X の型に懸念がある。"


def test_candidate_round_trips_explicit_fields_and_omits_absent_ones() -> None:
    candidate = DiscardedCandidate.from_json(EXPLICIT)
    assert candidate.kind is DiscardKind.UNVERIFIABLE_FROM_DIAGRAM
    assert candidate.to_json() == EXPLICIT
    assert DiscardedCandidate("S1", "r").to_json() == {"source": "S1", "reason": "r"}


def test_schema_accepts_new_fields_and_rejects_unknown_kind() -> None:
    validator = jsonschema.Draft202012Validator(macro.conclude_schema())
    base: dict[str, Any] = {"findings": [], "merged": []}
    assert not list(validator.iter_errors({**base, "discarded": [EXPLICIT]}))
    assert not list(
        validator.iter_errors({**base, "discarded": [{"source": "S1", "reason": "r"}]})
    )
    for bad in ({**EXPLICIT, "kind": "unknown"}, {**EXPLICIT, "concern": ""}):
        assert list(validator.iter_errors({**base, "discarded": [bad]}))


def test_boundary_resolves_the_pair_once_for_both_presence_cases() -> None:
    reason = "X の型は、図から確かめられない。"
    explicit = discard.discard_claim(reason, DiscardKind.NOT_A_CONTRACT, "Y の恐れがある。")
    assert explicit == discard.OrdinaryClaim(
        DiscardKind.NOT_A_CONTRACT, discard.ExtractedConcern("Y の恐れがある。")
    )
    read_back = discard.discard_claim(reason, None, None)
    assert isinstance(read_back, discard.OrdinaryClaim)
    assert read_back.kind is DiscardKind.UNVERIFIABLE_FROM_DIAGRAM
    assert read_back.concern == discard.ExtractedConcern("X の型に懸念がある。")
    broken = discard.discard_claim(reason, DiscardKind.OTHER, "途切れて、")
    assert isinstance(broken, discard.OrdinaryClaim)
    assert broken.concern == discard.NoConcern()


def test_nit_of_does_not_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_readback(monkeypatch)
    claim = discard.OrdinaryClaim(DiscardKind.OTHER, discard.NoConcern())
    decision = discard.nit_of("理由", claim)
    assert isinstance(decision, discard.Nit)
    assert decision.reason.claim == "理由"


def test_standards_template_table_is_generated_from_discard_kind() -> None:
    template = (Path(macro.__file__).parent / "prompts" / "macro_standards.md").read_text("utf-8")
    assert "$discard_reason_table" in template
    for kind in DiscardKind:
        assert f"| {kind.value} |" not in template
    standards = macro.prompt_parts()["standards"]
    assert "$discard_reason_table" not in standards
    assert discard.discard_reason_table() in standards
    for kind in (
        DiscardKind.UNVERIFIABLE_FROM_DIAGRAM,
        DiscardKind.DECIDED_BY_DECLARATION,
        DiscardKind.SETTLED_EARLIER,
        DiscardKind.OTHER,
    ):
        assert standards.count(f"| {kind.value} |") == 1


def test_kind_facts_table_has_every_kind_and_feeds_each_consumer() -> None:
    assert set(discard.DISCARD_KIND_FACTS) == set(DiscardKind)
    for kind in DiscardKind:
        assert kind.label == discard.DISCARD_KIND_FACTS[kind].label
    table = discard.discard_reason_table()
    for kind, reason in discard.DISCARD_REASONS:
        assert f"| {reason} | {kind.value} |" in table
    assert len({reason for _, reason in discard.DISCARD_REASONS}) == len(discard.DISCARD_REASONS)


def test_schema_kind_enum_equals_discard_kind() -> None:
    schema = macro.conclude_schema()
    kind = schema["properties"]["discarded"]["items"]["properties"]["kind"]
    assert kind["enum"] == [member.value for member in DiscardKind]


def test_kind_flags_and_read_back_come_from_the_table() -> None:
    assert [kind for kind in DiscardKind if not kind.becomes_nit] == [DiscardKind.SETTLED_EARLIER]
    assert [kind for kind in DiscardKind if kind.sent_to_micro] == [
        DiscardKind.UNVERIFIABLE_FROM_DIAGRAM
    ]
    # 種類を足すと、二つの並びのどちらかか、下の免除の集合に足すまで落ちる。
    # 契約にならない・深掘りは雛形の表に載せない種類、その他は文からは読み戻さない種類
    every = set(DiscardKind)
    not_in_template = {DiscardKind.NOT_A_CONTRACT, DiscardKind.DEEPENING}
    not_read_back = {DiscardKind.OTHER}
    assert {kind for kind, _ in discard.DISCARD_REASONS} == every - not_in_template
    assert {kind for kind, _ in discard.KIND_PATTERNS} == every - not_read_back
