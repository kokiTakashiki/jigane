import json
from pathlib import Path

import pytest
from review_fakes import write_fake_review

from jigane.groups.definitions import MACRO, MICRO
from jigane.reviews.copying import copy_review_directory
from jigane.reviews.lifecycle import (
    LocatedReview,
    ReviewError,
    load_review_at,
    read_review_at,
    set_repository_root,
)
from jigane.skills.pipeline import (
    PolicyGroupRun,
    ShardedGroupRun,
    read_group_run,
    read_runs,
    write_runs,
)


def _stored(finding_id: str) -> dict[str, object]:
    return {
        "id": finding_id,
        "severity": "warning",
        "viewpoint": "V",
        "claim": "c",
        "triage": None,
        "theme": None,
        "adjudication": None,
    }


def test_read_review_at_reads_a_copy_that_load_review_at_rejects(tmp_path: Path) -> None:
    directory = tmp_path / "elsewhere"
    write_fake_review(directory, tmp_path / "tree")

    located = read_review_at(directory)

    assert located.directory == directory
    assert located.review.repository_root == str(tmp_path / "tree")
    with pytest.raises(ReviewError):
        load_review_at(directory)


def test_read_review_at_reports_missing_review(tmp_path: Path) -> None:
    with pytest.raises(ReviewError):
        read_review_at(tmp_path / "none")


def test_set_repository_root_changes_only_the_root(tmp_path: Path) -> None:
    directory = tmp_path / "elsewhere"
    before = write_fake_review(directory, tmp_path / "tree")

    set_repository_root(read_review_at(directory), str(tmp_path / "other"))

    after = read_review_at(directory).review
    assert after.repository_root == str(tmp_path / "other")
    assert after.paths == before.paths
    assert after.id == before.id


ROW = {
    "role": "discovery",
    "split_index": 0,
    "input_tokens": 1,
    "output_tokens": 1,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "duration_ms": 1,
    "num_turns": 1,
    "is_error": False,
    "terminal_reason": None,
    "permission_denials": 0,
    "max_budget_usd": 0.5,
    "cost_usd": 0.25,
    "cost_source": "measured",
}


def test_read_group_run_reads_typed_values(tmp_path: Path) -> None:
    run = {
        "discovered": 5,
        "invocations": [ROW, {**ROW, "cost_usd": 0.5}],
        "discarded": [{"source": "S1", "reason": "r"}],
    }
    located = located_in(tmp_path)
    (located.directory / "runs.json").write_text(json.dumps({"macro": run}), encoding="utf-8")

    stored = read_group_run(located, MACRO)

    assert stored is not None
    assert stored.discovered == 5
    assert stored.cost_usd == pytest.approx(0.75)
    assert stored.considered is not None
    assert len(stored.considered.discarded) == 1
    assert read_group_run(located, MICRO) is None


def test_read_group_run_without_runs_file_is_none(tmp_path: Path) -> None:
    assert read_group_run(located_in(tmp_path), MACRO) is None


def test_group_run_round_trips_without_changing_the_stored_shape() -> None:
    stored = {
        "ran_at": "2026-01-01T00:00:00+00:00",
        "model": "m",
        "shape": "diagram",
        "discovered": 3,
        "invocations": [{**ROW, "cost_usd": 0.1}],
        "discarded": [{"source": "S1", "reason": "r"}],
        "budget_stops": [],
        "extra_key": 1,
    }

    assert ShardedGroupRun.from_json(stored).to_json() == stored


def test_group_run_from_an_old_record_does_not_add_keys() -> None:
    assert ShardedGroupRun.from_json({"discovered": 2}).to_json() == {"discovered": 2}
    assert ShardedGroupRun.from_json({"discovered": 2}).considered is None


def test_write_runs_round_trips_through_read_runs(tmp_path: Path) -> None:
    runs = {MACRO: PolicyGroupRun.from_json({"discovered": 1, "extra_key": 2})}

    write_runs(tmp_path, runs)

    assert read_runs(tmp_path) == runs


def located_in(tmp_path: Path) -> LocatedReview:
    directory = tmp_path / "located"
    write_fake_review(directory, tmp_path / "tree")
    return read_review_at(directory)


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    write_fake_review(source, source / "tree")
    (source / "findings.json").write_text(
        json.dumps(
            {"last_number": 2, "groups": {"macro": [_stored("F1")], "micro": [_stored("F2")]}}
        ),
        encoding="utf-8",
    )
    for name in ("findings_history.json", "selection.json", "runs.json", "run_in_progress.json"):
        (source / name).write_text("{}", encoding="utf-8")
    return source


def test_copy_clears_own_state_and_moves_the_root(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    destination = tmp_path / "destination"

    copy_review_directory(source, destination, clear_group=MACRO)

    assert read_review_at(destination).review.repository_root == str(destination / "tree")
    state = json.loads((destination / "findings.json").read_text(encoding="utf-8"))
    assert list(state["groups"]) == ["micro"]
    for name in ("findings_history.json", "selection.json", "runs.json", "run_in_progress.json"):
        assert not (destination / name).exists()
    assert (source / "runs.json").exists()
    assert read_review_at(source).review.repository_root == str(source / "tree")


def test_copy_without_clear_group_keeps_state(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    destination = tmp_path / "destination"

    copy_review_directory(source, destination)

    assert (destination / "runs.json").exists()
    state = json.loads((destination / "findings.json").read_text(encoding="utf-8"))
    assert set(state["groups"]) == {"macro", "micro"}
