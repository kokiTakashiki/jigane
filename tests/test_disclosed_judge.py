"""判定役の disclosed_at を、棄却ではなく種類つきの nit として取り込む。"""

from pathlib import Path

import jsonschema
import pytest
from fakes import FakeInvoke, MicroFake, d_finding, settings_for
from test_adversarial import make_candidate, run_verify
from test_security import SecurityFake, s_finding

from jigane.findings.discard import DiscardKind
from jigane.findings.records import Triage, Verdict, load_findings_state, triaged_dicts
from jigane.groups.definitions import MICRO, SECURITY
from jigane.llm_runs.finding_model import KEY_DISCLOSED_AT
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import adversarial, micro, pipeline, security
from jigane.skills.assembly import AssemblyInput

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


@pytest.mark.parametrize("name", ["judge_rebuttal", "judge_alternative"])
def test_judge_schema_has_an_optional_disclosed_at(name: str) -> None:
    item = adversarial.role_schemas()[name]["properties"]["items"]["items"]
    assert item["properties"][KEY_DISCLOSED_AT]["type"] == "string"
    assert item["properties"][KEY_DISCLOSED_AT]["minLength"] == 1
    assert KEY_DISCLOSED_AT not in item["required"]


def test_judge_schema_accepts_a_rejected_verdict_with_disclosed_at() -> None:
    schema = adversarial.role_schemas()["judge_alternative"]
    sample = {"index": 0, "verdict": "rejected", "reason": "r", KEY_DISCLOSED_AT: PLACE}
    jsonschema.validate({"items": [sample]}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"items": [{**sample, KEY_DISCLOSED_AT: ""}]}, schema)


@pytest.mark.parametrize("viewpoint", ["D1", "M2"])
def test_rejected_with_disclosed_at_becomes_a_kinded_nit_and_keeps_the_adjudication(
    viewpoint: str,
) -> None:
    fake = FakeInvoke(verdict="rejected")
    fake.disclosed_at = PLACE
    outcome = run_verify([make_candidate(viewpoint, "上限が無い")], fake)
    [verified] = outcome.findings
    assert verified.adjudication.verdict is Verdict.REJECTED
    assert verified.candidate["severity"] == "nit"
    assert KEY_DISCLOSED_AT not in verified.candidate
    reason = verified.candidate["nit_reason"]
    assert reason["kind"] == DiscardKind.DISCLOSED_LIMITATION.value
    assert reason["text"] == "上限が無い"
    assert reason["concern"] == "上限が無い"
    assert reason["places"] == [PLACE]


def test_rejected_without_disclosed_at_is_unchanged() -> None:
    outcome = run_verify([make_candidate("D1")], FakeInvoke(verdict="rejected"))
    [verified] = outcome.findings
    assert verified.candidate == make_candidate("D1")


@pytest.mark.parametrize("verdict", ["kept", "weakened"])
def test_disclosed_at_on_another_verdict_is_ignored(verdict: str) -> None:
    fake = FakeInvoke(verdict=verdict)
    fake.disclosed_at = PLACE
    outcome = run_verify([make_candidate("D1"), make_candidate("M2")], fake)
    assert [f.candidate for f in outcome.findings] == [make_candidate("D1"), make_candidate("M2")]
    assert [f.adjudication.verdict.value for f in outcome.findings] == [verdict, verdict]


def test_micro_rejected_with_disclosed_at_is_folded_not_rejected(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [d_finding("D1"), d_finding("D2")], 2: []}, verdict="rejected")
    fake.disclosed_at = PLACE
    result = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    found = {f["claim"]: f for f in triaged_dicts(result.findings)}
    assert {f["triage"] for f in found.values()} == {Triage.FOLDED}
    assert found["claim D1"]["severity"] == "nit"
    assert found["claim D1"]["nit_reason"]["kind"] == "disclosed_limitation"
    assert found["claim D1"]["adjudication"]["verdict"] == "rejected"
    saved = load_findings_state(review.id.directory()).groups[MICRO]
    assert all(f.finding.nit_reason is not None for f in saved)


def test_micro_bundles_disclosed_nits_and_counts_the_invocation(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [d_finding("D1"), d_finding("D2")], 2: []}, verdict="rejected")
    fake.disclosed_at = PLACE
    fake.bundle_groups = [[0, 1]]
    result = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    assert len(triaged_dicts(result.findings)) == 1
    roles = [i.role for i in pipeline.all_invocations(result.run_state)]
    assert roles.count("bundle_disclosed") == 1
    assert result.run_state.others["disclosed_bundling"]["groups"] == 1


def test_micro_rejected_without_disclosed_at_stays_rejected(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [d_finding("D1")], 2: []}, verdict="rejected")
    result = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    assert [f["triage"] for f in triaged_dicts(result.findings)] == [Triage.REJECTED]


def test_security_rejected_with_disclosed_at_remains(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = SecurityFake({1: [s_finding("S-AUTH", severity="critical")], 2: []}, verdict="rejected")
    fake.disclosed_at = PLACE
    result = security.run_security(AssemblyInput.load(review), settings_for(fake))
    found = triaged_dicts(result.findings)
    assert [(f["severity"], f["triage"]) for f in found] == [("nit", Triage.REMAINING)]
    assert found[0]["nit_reason"]["kind"] == "disclosed_limitation"
    saved = load_findings_state(review.id.directory()).groups[SECURITY]
    assert [f.finding.nit_reason.kind for f in saved if f.finding.nit_reason] == [
        DiscardKind.DISCLOSED_LIMITATION
    ]


def test_security_bundles_disclosed_nits_and_counts_the_invocation(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    shard_findings = [
        s_finding("S-AUTH", severity="critical"),
        s_finding("S-CRYPTO", severity="critical"),
    ]
    fake = SecurityFake({1: shard_findings, 2: []}, verdict="rejected")
    fake.disclosed_at = PLACE
    fake.bundle_groups = [[0, 1]]
    result = security.run_security(AssemblyInput.load(review), settings_for(fake))
    assert len(triaged_dicts(result.findings)) == 1
    roles = [i.role for i in pipeline.all_invocations(result.run_state)]
    assert roles.count("bundle_disclosed") == 1


def test_kept_with_disclosed_at_stays_a_normal_finding(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [d_finding("D1")], 2: []}, verdict="kept")
    fake.disclosed_at = PLACE
    result = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    [found] = triaged_dicts(result.findings)
    assert (found["severity"], found["triage"]) == ("warning", Triage.MUST_READ)
    assert found.get("nit_reason") is None
