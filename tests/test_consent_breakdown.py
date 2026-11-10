from pathlib import Path

import pytest
from fakes import settings_for

from jigane.commands import consent
from jigane.groups.definitions import MACRO, MICRO, SECURITY
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import estimator
from jigane.skills.assembly import AssemblyInput
from jigane.skills.group_expectation import GroupExpectation, VerifierMultiplierSource


def never_invoked(_invocation: claude.Invocation) -> claude.InvocationResult:
    raise AssertionError("見積もりは起動しない")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_the_breakdown_takes_calls_and_verifier_from_the_selected_estimate(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assembly = AssemblyInput.load(review)

    for group, has_verifier in ((MACRO, False), (MICRO, True), (SECURITY, True)):
        settings = settings_for(never_invoked)
        estimate = estimator.estimate_of(assembly, group, budget=settings.budget)
        plan = consent.launch_plan(
            estimate,
            GroupExpectation(0.2, 1.0, VerifierMultiplierSource.NO_VERIFIER),
            group,
            settings,
        )
        assert plan.breakdown.group == group
        assert plan.group == group
        assert plan.breakdown.discovery_calls == estimate.discovery_calls
        assert plan.breakdown.has_verifier == estimate.has_verifier
        assert plan.breakdown.has_verifier is has_verifier
