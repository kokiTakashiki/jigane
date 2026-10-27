"""発見役の disclosed_at を、種類つきの nit に直す取り込み。"""

from pathlib import Path
from typing import Any

import pytest
from fakes import MicroFake, d_finding, settings_for
from test_security import SecurityFake, s_finding

from jigane.findings.discard import DiscardKind, DisclosedNitReason, ExtractedConcern
from jigane.findings.records import Triage, load_findings_state, triaged_dicts
from jigane.groups.definitions import MICRO, SECURITY
from jigane.llm_runs.finding_model import KEY_DISCLOSED_AT
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import micro, security, shared_rules
from jigane.skills.assembly import AssemblyInput
from jigane.skills.disclosed import disclosed_as_nit

PLACE = "docs/design.md の限界の節"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    for name in ("a", "b"):
        (root / "src" / f"{name}.py").write_text(
            f"# {name}\n" + "x = 1\n" * 3500, encoding="utf-8"
        )
    monkeypatch.chdir(root)
    return root


def disclosed(finding: dict[str, Any], severity: str = "nit") -> dict[str, Any]:
    return {**finding, "severity": severity, KEY_DISCLOSED_AT: PLACE}


@pytest.mark.parametrize("schema", [micro.discovery_schema(), security.discovery_schema()])
def test_discovery_schema_has_an_optional_disclosed_at(schema: dict[str, Any]) -> None:
    item = schema["properties"]["findings"]["items"]
    assert item["properties"][KEY_DISCLOSED_AT]["type"] == "string"
    assert item["properties"][KEY_DISCLOSED_AT]["minLength"] == 1
    assert KEY_DISCLOSED_AT not in item["required"]


def test_candidate_without_disclosed_at_is_returned_unchanged() -> None:
    candidate = {"claim": "c", "severity": "warning", "viewpoint": "D1"}
    assert disclosed_as_nit(candidate) is candidate


def test_disclosed_at_becomes_a_disclosed_limitation_nit() -> None:
    candidate = {"claim": "上限が無い", "severity": "nit", "viewpoint": "D1", "evidence": [1]}
    result = disclosed_as_nit({**candidate, KEY_DISCLOSED_AT: PLACE})
    reason = DisclosedNitReason(
        "上限が無い",
        ExtractedConcern("上限が無い"),
        (PLACE,),
    )
    assert result == {**candidate, "nit_reason": reason.to_json()}


@pytest.mark.parametrize("schema", [micro.discovery_schema(), security.discovery_schema()])
def test_disclosed_at_description_asks_to_merge_places(schema: dict[str, Any]) -> None:
    item = schema["properties"]["findings"]["items"]
    description = item["properties"][KEY_DISCLOSED_AT]["description"]
    assert "読点で区切って並べる" in description
    assert "一件にまとめ" not in description


def test_scope_rule_asks_to_merge_one_limitation_into_one_finding() -> None:
    scope_rule = shared_rules.prompt_text("scope_rule")
    assert "同じ限界が複数の場所で開示されていても一件にまとめます" in scope_rule


def test_several_places_in_disclosed_at_become_one_nit_reason() -> None:
    places = "docs/design.md の限界の節、ロックファイルの note、決定 D3"
    candidate = {"claim": "jar の出どころを検証しない", "severity": "warning", "viewpoint": "D1"}
    result = disclosed_as_nit({**candidate, KEY_DISCLOSED_AT: places})
    assert result["nit_reason"]["concern"] == "jar の出どころを検証しない"
    assert result["nit_reason"]["places"] == places.split("、")


@pytest.mark.parametrize("severity", ["critical", "warning"])
def test_disclosed_at_forces_severity_to_nit(severity: str) -> None:
    candidate = {"claim": "c", "severity": severity, "viewpoint": "D1", KEY_DISCLOSED_AT: PLACE}
    result = disclosed_as_nit(candidate)
    assert result["severity"] == "nit"
    assert result["viewpoint"] == "D1"
    assert KEY_DISCLOSED_AT not in result


def test_micro_disclosed_finding_is_stored_with_its_kind_and_folded(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [disclosed(d_finding("D1"), "warning"), d_finding("D2")], 2: []})
    result = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    saved = {f.finding.claim: f for f in load_findings_state(review.id.directory()).groups[MICRO]}
    body = saved["claim D1"].finding
    assert body.severity == "nit"
    assert body.viewpoint == "D1"
    assert body.extra["evidence"]
    assert body.nit_reason is not None
    assert isinstance(body.nit_reason, DisclosedNitReason)
    assert body.nit_reason.claim == "claim D1"
    assert body.nit_reason.places == (PLACE,)
    assert saved["claim D2"].finding.nit_reason is None
    found = {f["claim"]: f for f in triaged_dicts(result.findings)}
    assert found["claim D1"]["triage"] == Triage.FOLDED
    assert found["claim D1"]["adjudication"] is None
    assert found["claim D1"]["nit_reason"]["kind"] == "disclosed_limitation"
    assert found["claim D2"]["triage"] == Triage.MUST_READ


def test_security_disclosed_finding_is_stored_with_its_kind_and_remains(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = SecurityFake({1: [disclosed(s_finding("S-AUTH"), "critical")], 2: []})
    result = security.run_security(AssemblyInput.load(review), settings_for(fake))
    saved = load_findings_state(review.id.directory()).groups[SECURITY]
    assert [f.finding.nit_reason.kind for f in saved if f.finding.nit_reason] == [
        DiscardKind.DISCLOSED_LIMITATION
    ]
    found = triaged_dicts(result.findings)
    assert [(f["severity"], f["triage"]) for f in found] == [("nit", Triage.REMAINING)]
