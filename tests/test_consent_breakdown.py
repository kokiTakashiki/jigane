from pathlib import Path

import pytest
from fakes import settings_for

from jigane.commands import consent
from jigane.groups.definitions import MACRO, MICRO, SECURITY
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import estimator
from jigane.skills.assembly import AssemblyInput


def never_invoked(_invocation: claude.Invocation) -> claude.InvocationResult:
    raise AssertionError("見積もりは起動しない")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_the_breakdown_takes_calls_and_verifier_from_each_estimate(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assembly = AssemblyInput.load(review)

    plan = consent.launch_plan(assembly, MICRO, settings_for(never_invoked))

    by_group = {item.group: item for item in plan.groups}
    for group in (MACRO, MICRO, SECURITY):
        estimate = estimator.estimate_of(assembly, group)
        item = by_group[group]
        assert item.discovery_calls == estimate.discovery_calls
        assert item.has_verifier == estimate.has_verifier
    assert by_group[MACRO].has_verifier is False
    assert by_group[MICRO].has_verifier is True
