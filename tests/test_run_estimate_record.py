from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import MicroFake, settings_for

from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.subcommands.run import plan_of, run_group, run_review
from jigane.groups.definitions import MICRO
from jigane.reviews.catalog import load_review
from jigane.reviews.lifecycle import create_feature_review
from jigane.reviews.resolution import resolve_review
from jigane.skills import pipeline
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimator import estimate_of
from jigane.skills.group_expectation import (
    DEFAULT_VERIFIER_MULTIPLIER,
    GroupExpectation,
    VerifierMultiplierSource,
    read_expectation,
)
from jigane.skills.mcp import NO_MCP


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _ran_micro(root: Path, name: str) -> pipeline.GroupRun:
    review_id = str(create_feature_review(root, ["src"], name=name).id)
    assert run_review(review_id, MICRO, invoke=MicroFake({1: []})) == EXIT_OK
    run = pipeline.read_runs(load_review(resolve_review(review_id)).id.directory()).get(MICRO)
    assert run is not None
    return run


def _restamp(root: Path, name: str, stamp: pipeline.EstimateStamp) -> None:
    review = load_review(resolve_review(f"{root.name}:{name}"))
    run = pipeline.read_runs(review.id.directory())[MICRO]
    pipeline.record_estimate(
        review.id.directory(), MICRO, stamp, datetime.fromisoformat(run.ran_at or "")
    )


def _expectation(root: Path) -> GroupExpectation:
    review = create_feature_review(root, ["src"], name="reader")
    budget = settings_for(MicroFake({1: []})).budget
    estimate = estimate_of(AssemblyInput.load(review), MICRO, budget=budget)
    return read_expectation(estimate, budget, root, MICRO)


def test_run_writes_estimate_and_counting_version_to_the_round_record(root: Path) -> None:
    stamp = _ran_micro(root, "one").estimate_stamp

    assert stamp is not None
    assert stamp.discovery_usd > 0
    assert stamp.counting_version == pipeline.ESTIMATE_COUNTING_VERSION


def test_record_without_the_fields_reads_as_no_estimate() -> None:
    run = pipeline.ShardedGroupRun.from_json({"ran_at": "2026-01-01T00:00:00+00:00"})

    assert run.estimate_stamp is None
    assert pipeline.EstimateStamp.from_json({pipeline.ESTIMATE_USD_KEY: 1.0}) is None


@pytest.mark.parametrize(
    "stored",
    [
        {"discovery_estimate_usd": True, "estimate_version": 1},
        {"discovery_estimate_usd": 0.1, "estimate_version": True},
        {"discovery_estimate_usd": 0.1, "estimate_version": "1"},
        {"discovery_estimate_usd": 0.1, "estimate_version": 1.5},
        {"discovery_estimate_usd": "0.1", "estimate_version": 1},
    ],
)
def test_stamp_with_a_non_numeric_field_reads_as_no_estimate(stored: dict[str, object]) -> None:
    assert pipeline.EstimateStamp.from_json(stored) is None


def test_stamp_is_not_kept_in_others_and_keeps_the_stored_field_names() -> None:
    run = pipeline.ShardedGroupRun.from_json(
        {"discovery_estimate_usd": 0.25, "estimate_version": 1, "unknown": 1}
    )

    assert run.estimate_stamp == pipeline.EstimateStamp(0.25, 1)
    assert run.others == {"unknown": 1}
    stored = run.to_json()
    assert stored["discovery_estimate_usd"] == 0.25
    assert stored["estimate_version"] == 1


def test_a_start_after_the_latest_record_leaves_the_earlier_round_unstamped(root: Path) -> None:
    run = _ran_micro(root, "one")
    review = load_review(resolve_review(f"{root.name}:one"))
    later = datetime.fromisoformat(run.ran_at or "") + timedelta(hours=1)

    pipeline.record_estimate(
        review.id.directory(),
        MICRO,
        pipeline.EstimateStamp(9.0, pipeline.ESTIMATE_COUNTING_VERSION),
        later.astimezone(UTC),
    )

    stored = pipeline.read_runs(review.id.directory())[MICRO]
    assert stored.estimate_stamp == run.estimate_stamp
    assert stored.estimate_stamp != pipeline.EstimateStamp(9.0, pipeline.ESTIMATE_COUNTING_VERSION)


def test_stamp_survives_a_round_trip_of_runs_json(root: Path) -> None:
    run = _ran_micro(root, "one")
    restored = pipeline.ShardedGroupRun.from_json(run.to_json())

    assert restored.estimate_stamp == run.estimate_stamp


def test_multiplier_is_actual_cost_over_the_discovery_estimate(root: Path) -> None:
    run = _ran_micro(root, "one")
    _restamp(root, "one", pipeline.EstimateStamp(0.04, pipeline.ESTIMATE_COUNTING_VERSION))

    result = _expectation(root)

    assert result.source is VerifierMultiplierSource.RUN_RECORDS
    assert result.multiplier == pytest.approx(run.cost_usd / 0.04)


def test_rounds_of_another_counting_version_are_not_mixed(root: Path) -> None:
    run = _ran_micro(root, "current")
    _ran_micro(root, "old")
    _restamp(root, "current", pipeline.EstimateStamp(0.05, pipeline.ESTIMATE_COUNTING_VERSION))
    _restamp(root, "old", pipeline.EstimateStamp(0.001, pipeline.ESTIMATE_COUNTING_VERSION + 1))

    assert _expectation(root).multiplier == pytest.approx(run.cost_usd / 0.05)


def test_read_expectation_defaults_the_multiplier_without_round_records(root: Path) -> None:
    result = _expectation(root)

    assert result.multiplier == DEFAULT_VERIFIER_MULTIPLIER
    assert result.source is VerifierMultiplierSource.DEFAULT


def test_run_group_stamps_the_expectation_of_the_plan_it_was_given(root: Path) -> None:
    review = create_feature_review(root, ["src"], name="given")
    settings = settings_for(MicroFake({1: []}))
    assembly = AssemblyInput.load(review)
    given = GroupExpectation(0.123, 2.0, VerifierMultiplierSource.RUN_RECORDS)
    plan = replace(plan_of(assembly, MICRO, settings, NO_MCP), expectation=given)

    run_group(assembly, settings, plan=plan)

    stored = pipeline.read_runs(review.id.directory())[MICRO]
    assert stored.estimate_stamp == pipeline.EstimateStamp(
        0.123, pipeline.ESTIMATE_COUNTING_VERSION
    )


def test_a_malformed_ran_at_leaves_the_round_unstamped(root: Path) -> None:
    run = _ran_micro(root, "one")
    directory = load_review(resolve_review(f"{root.name}:one")).id.directory()
    runs = pipeline.read_runs(directory)
    runs[MICRO] = replace(run, ran_at="not-a-timestamp", estimate_stamp=None)
    pipeline.write_runs(directory, runs)

    pipeline.record_estimate(
        directory,
        MICRO,
        pipeline.EstimateStamp(9.0, pipeline.ESTIMATE_COUNTING_VERSION),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert pipeline.read_runs(directory)[MICRO].estimate_stamp is None
