import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from fakes import MacroFailure, MacroFake, _single_viewpoint, discovered
from keep_previous_support import extras, first_run, runs_json, settings, stored

from jigane.findings.records import load_findings_state, triaged_dicts
from jigane.groups.policy_runs import PolicyRole, PolicyShape
from jigane.llm_runs.launch_failure import RunRecordKind
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro, pipeline, policy_keep, policy_launch, policy_reason, policy_record
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_launch import LaunchResult
from jigane.skills.policy_reason import DiagramNotRun


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _policy_run(review: Review) -> pipeline.PolicyGroupRun:
    run = pipeline.read_runs(review.id.directory())[macro.GROUP]
    assert isinstance(run, pipeline.PolicyGroupRun)
    return run


def test_failed_diagram_launch_keeps_the_previous_policy_findings(project: Path) -> None:
    review = first_run(project)
    before = stored(review)
    assert {item["viewpoint"] for item in before} == {"M4", "P1"}
    fake = MacroFake([discovered("P1")], fails=MacroFailure.DIAGRAM)
    result = macro.run_macro(AssemblyInput.load(review), settings(fake), extras=extras())
    assert stored(review) == before
    assert triaged_dicts(result.findings) == before
    assert policy_reason.diagram_not_run(result.run_state) is DiagramNotRun.FAILED
    state = runs_json(review)["macro"]
    assert state["record_kind"] == RunRecordKind.SUCCEEDED_THEN_FAILED.value
    (failure,) = state["launch_failures"]
    assert failure["role"] == PolicyRole.DIAGRAM.value


def test_stopped_diagram_launch_keeps_the_previous_policy_findings(project: Path) -> None:
    review = first_run(project)
    before = stored(review)
    fake = MacroFake([discovered("P1")])
    result = macro.run_macro(
        AssemblyInput.load(review), settings(fake, diagram_usd=0.0001), extras=extras()
    )
    assert [_single_viewpoint(call) for call in fake.calls] == ["P1"]
    assert stored(review) == before
    assert triaged_dicts(result.findings) == before
    assert policy_reason.diagram_not_run(result.run_state) is DiagramNotRun.STOPPED_FOR_INPUT
    state = runs_json(review)["macro"]
    assert state["record_kind"] == RunRecordKind.SUCCEEDED_THEN_FAILED.value
    assert [stop["index"] for stop in state["budget_stops"]] == [0]
    assert not state.get("launch_failures")


def test_first_run_without_diagram_stores_no_findings(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("P1")], fails=MacroFailure.DIAGRAM)
    result = macro.run_macro(AssemblyInput.load(review), settings(fake), extras=extras())
    assert result.findings == ()
    assert policy_reason.diagram_not_run(result.run_state) is None
    assert macro.GROUP not in load_findings_state(review.id.directory()).groups
    assert runs_json(review)["macro"]["record_kind"] == RunRecordKind.FAILED_ONLY.value


def test_failed_principle_launch_still_replaces_with_the_diagram_findings(project: Path) -> None:
    review = first_run(project)
    fake = MacroFake([discovered("M4")], fails=MacroFailure.PRINCIPLES)
    result = macro.run_macro(AssemblyInput.load(review), settings(fake), extras=extras())
    assert policy_reason.diagram_not_run(result.run_state) is None
    assert [item["viewpoint"] for item in stored(review)] == ["M4"]
    assert runs_json(review)["macro"]["record_kind"] == RunRecordKind.SUCCEEDED.value


def test_the_record_gives_the_same_reason_as_the_run(project: Path) -> None:
    review = first_run(project)
    assert policy_reason.diagram_not_run(None) is None
    assert policy_reason.diagram_not_run(_policy_run(review)) is None
    macro.run_macro(
        AssemblyInput.load(review),
        settings(MacroFake([discovered("P1")], fails=MacroFailure.DIAGRAM)),
        extras=extras(),
    )
    assert policy_reason.diagram_not_run(_policy_run(review)) is DiagramNotRun.FAILED
    macro.run_macro(
        AssemblyInput.load(review),
        settings(MacroFake([discovered("P1")]), diagram_usd=0.0001),
        extras=extras(),
    )
    assert policy_reason.diagram_not_run(_policy_run(review)) is DiagramNotRun.STOPPED_FOR_INPUT


def test_all_failed_run_records_the_stopped_launches_too(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("P1")], fails=MacroFailure.DIAGRAM)
    with pytest.raises(policy_launch.LaunchFailed):
        macro.run_macro(
            AssemblyInput.load(review), settings(fake, principle_usd=0.0000001), extras=extras()
        )
    state = runs_json(review)["macro"]
    assert [stop["index"] for stop in state["budget_stops"]] == [1]
    (failure,) = state["launch_failures"]
    assert failure["role"] == PolicyRole.DIAGRAM.value


@pytest.mark.parametrize(
    ("diagram_usd", "fails", "expected"),
    [
        (0.5, MacroFailure.DIAGRAM, DiagramNotRun.FAILED),
        (0.0001, None, DiagramNotRun.STOPPED_FOR_INPUT),
        (0.5, MacroFailure.PRINCIPLES, None),
    ],
)
def test_the_run_side_judgement_matches_the_one_derived_from_the_record(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagram_usd: float,
    fails: MacroFailure | None,
    expected: DiagramNotRun | None,
) -> None:
    review = first_run(project)
    judged: list[DiagramNotRun | None] = []
    classify = policy_keep.classify_launches

    def spy(
        context: policy_record.RunContext, results: Sequence[LaunchResult]
    ) -> policy_keep.Classified:
        classified = classify(context, results)
        judged.append(classified.reason)
        return classified

    monkeypatch.setattr(policy_keep, "classify_launches", spy)
    fake = MacroFake([discovered("M4"), discovered("P1")], fails=fails)
    macro.run_macro(
        AssemblyInput.load(review), settings(fake, diagram_usd=diagram_usd), extras=extras()
    )
    assert judged == [expected]
    assert policy_reason.diagram_not_run(_policy_run(review)) is expected


def test_a_previous_record_without_a_shape_gets_this_runs_shape(project: Path) -> None:
    review = first_run(project)
    path = review.id.directory() / "runs.json"
    runs = json.loads(path.read_text("utf-8"))
    del runs["macro"]["shape"]
    path.write_text(json.dumps(runs), encoding="utf-8")
    fake = MacroFake([discovered("P1")], fails=MacroFailure.DIAGRAM)
    result = macro.run_macro(AssemblyInput.load(review), settings(fake), extras=extras())
    assert runs_json(review)["macro"]["shape"] == PolicyShape.DIAGRAM.value
    assert policy_reason.diagram_not_run(result.run_state) is DiagramNotRun.FAILED
    assert policy_reason.diagram_not_run(_policy_run(review)) is DiagramNotRun.FAILED


def test_the_returned_record_is_the_saved_one_so_run_and_show_totals_agree(
    project: Path,
) -> None:
    review = first_run(project)
    for _ in range(2):
        fake = MacroFake([discovered("P1")], fails=MacroFailure.DIAGRAM)
        result = macro.run_macro(AssemblyInput.load(review), settings(fake), extras=extras())
        saved = _policy_run(review)
        assert result.run_state.to_json() == saved.to_json()
        assert pipeline.summarize_invocations(result.run_state) == pipeline.summarize_invocations(
            saved
        )
