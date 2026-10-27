"""実装の群とセキュリティの群の発見役が出す範囲外の印（in_scope と scope_reason）。"""

from pathlib import Path
from typing import Any

import pytest
from fakes import FakeInvoke, MicroFake, d_finding, settings_for
from test_security import SecurityFake, s_finding

from jigane.findings.discard import ShardConcerns
from jigane.findings.records import OutOfScopeDecision, Triage, triaged_dicts
from jigane.groups.definitions import MICRO, SECURITY, GroupId
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro, micro, security, sharded, shards
from jigane.skills.assembly import AssemblyInput

REASON = "最初の一手が範囲の外のファイルに及ぶ"


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


def out_of_scope(finding: dict[str, Any]) -> dict[str, Any]:
    return {**finding, "in_scope": False, "scope_reason": REASON}


def verifier_prompts(fake: FakeInvoke) -> str:
    return "\n".join(call.prompt for call in fake.calls if "担当のシャード" not in call.prompt)


def by_claim(findings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f["claim"]: f for f in findings}


def test_micro_out_of_scope_gets_the_triage_and_record_and_skips_verification(
    project: Path,
) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [d_finding("D1"), out_of_scope(d_finding("D2"))], 2: []})
    result = micro.run_micro(AssemblyInput.load(review), settings_for(fake))
    found = by_claim(triaged_dicts(result.findings))
    assert found["claim D2"]["triage"] == "out_of_scope"
    assert found["claim D2"]["out_of_scope"] == {"paths": [], "reason": REASON}
    assert found["claim D2"]["adjudication"] is None
    assert found["claim D2"]["theme"] is None
    assert "in_scope" not in found["claim D2"]
    assert found["claim D1"]["triage"] == "must_read"
    assert found["claim D1"]["adjudication"] is not None
    assert "out_of_scope" not in found["claim D1"]
    assert "claim D2" not in verifier_prompts(fake)
    decision = next(f for f in result.findings if f.finding.claim == "claim D2").decision
    assert isinstance(decision, OutOfScopeDecision)
    assert decision.triage is Triage.OUT_OF_SCOPE


def test_security_out_of_scope_gets_the_triage_and_record_and_skips_verification(
    project: Path,
) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = SecurityFake({1: [s_finding("S-STORAGE"), out_of_scope(s_finding("S-AUTH"))], 2: []})
    result = security.run_security(AssemblyInput.load(review), settings_for(fake))
    found = by_claim(triaged_dicts(result.findings))
    assert found["claim S-AUTH"]["triage"] == "out_of_scope"
    assert found["claim S-AUTH"]["out_of_scope"] == {"paths": [], "reason": REASON}
    assert found["claim S-AUTH"]["adjudication"] is None
    assert found["claim S-STORAGE"]["triage"] == "must_read"
    assert found["claim S-STORAGE"]["adjudication"] is not None
    assert "out_of_scope" not in found["claim S-STORAGE"]
    assert "claim S-AUTH" not in verifier_prompts(fake)


def test_out_of_scope_nit_stays_out_of_scope(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    nit = out_of_scope({**s_finding("S-AUTH"), "severity": "nit"})
    fake = SecurityFake({1: [nit], 2: []})
    result = security.run_security(AssemblyInput.load(review), settings_for(fake))
    assert [f["triage"] for f in triaged_dicts(result.findings)] == ["out_of_scope"]


def test_discovery_without_the_scope_fields_is_a_bad_output(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    bare = {k: v for k, v in s_finding().items() if k not in ("in_scope", "scope_reason")}
    fake = SecurityFake({1: [bare], 2: []})
    result = security.run_security(AssemblyInput.load(review), settings_for(fake))
    assert triaged_dicts(result.findings) == []
    failures = result.run_state.launch_failures or ()
    assert [f.kind for f in failures] == [claude.FailureKind.BAD_OUTPUT]


SCOPE_RULE_SENTENCE = (
    "範囲で直せないものも出し、in_scope を false にして scope_reason に理由を一文で書きます"
)


@pytest.mark.parametrize("group", [MICRO, SECURITY])
def test_shard_prompt_carries_the_scope_rule_once(project: Path, group: GroupId) -> None:
    review = create_feature_review(project, ["src"], "p")
    [shard, *_] = shards.build_plan(review, group).shards
    prompt = sharded.build_shard_prompt(
        group, AssemblyInput.load(review), shard, 1, 1, concerns=ShardConcerns()
    )
    assert prompt.count(SCOPE_RULE_SENTENCE) == 1
    assert "$scope_rule" not in prompt


def test_policy_prompt_carries_the_scope_rule_once(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert prompt.count(SCOPE_RULE_SENTENCE) == 1
    assert "$scope_rule" not in prompt
