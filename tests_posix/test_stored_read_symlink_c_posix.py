from collections.abc import Callable
from pathlib import Path

import pytest
from test_review_relations import save

from jigane.findings.records import IN_PROGRESS_FILE, RUNS_FILE
from jigane.judgment_map.changes import CHANGES_FILE
from jigane.reviews.lifecycle import Review
from jigane.skills import macro, pipeline, policy_value_sets, run_record
from jigane.static_analysis import intake
from jigane.storage.review_error import ReviewError
from jigane.storage.store import ReviewKind
from jigane.storage.symlink_guard import StoredFileSymlinkError

OUTSIDE_TEXT = "{}"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _link(directory: Path, name: str, tmp_path: Path) -> None:
    """保存先のファイルを、外のファイルへの symlink にする（元のファイルがあれば差し替える）。"""
    outside = tmp_path / f"outside-{name}"
    outside.write_text(OUTSIDE_TEXT, encoding="utf-8")
    (directory / name).unlink(missing_ok=True)
    (directory / name).symlink_to(outside)


def _diff_review(project: Path) -> Review:
    return save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")


@pytest.mark.parametrize(
    "read",
    [
        macro.changes_prompt_text,
        macro.verifier_changes_text,
        policy_value_sets._added_lines,
    ],
)
def test_changes_file_readers_refuse_a_symlink(
    project: Path, tmp_path: Path, read: Callable[[Review], object]
) -> None:
    review = _diff_review(project)
    _link(review.id.directory(), CHANGES_FILE, tmp_path)
    with pytest.raises(StoredFileSymlinkError):
        read(review)


@pytest.mark.parametrize(
    ("read", "name"),
    [
        (pipeline.read_runs, RUNS_FILE),
        (run_record._read_in_progress, IN_PROGRESS_FILE),
        (run_record._read_stopped, run_record.STOPPED_FILE),
    ],
)
def test_run_record_readers_refuse_a_symlink(
    tmp_path: Path, read: Callable[[Path], object], name: str
) -> None:
    directory = tmp_path / "review"
    directory.mkdir()
    _link(directory, name, tmp_path)
    with pytest.raises(StoredFileSymlinkError):
        read(directory)


def test_run_record_readers_return_empty_for_a_missing_file(tmp_path: Path) -> None:
    assert pipeline.read_runs(tmp_path) == {}
    assert run_record._read_in_progress(tmp_path) == {}
    assert run_record._read_stopped(tmp_path) == {}


def test_read_static_findings_turns_a_symlink_into_a_review_error(tmp_path: Path) -> None:
    _link(tmp_path, intake.STATIC_FINDINGS_FILE, tmp_path)
    with pytest.raises(ReviewError):
        intake.read_static_findings(tmp_path)


def test_read_static_findings_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    assert intake.read_static_findings(tmp_path) is None
