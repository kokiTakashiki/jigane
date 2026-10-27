import json
from pathlib import Path

import pytest
from review_fakes import save_review_of_kind

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands.refresh import refresh_review
from jigane.reviews.lifecycle import (
    ARCHIVE_MARKER_FILE,
    ARCHIVED_INCLUDE,
    ARCHIVED_ONLY,
    create_feature_review,
    is_archived,
    list_reviews,
    resolve_review,
)
from jigane.storage.store import ReviewId, ReviewKind


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_archive_and_unarchive_report_state(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "old")
    assert main(["archive", str(review.id), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {
        "id": str(review.id),
        "archived": True,
        "changed": True,
    }
    assert is_archived(review.id)
    # すでにアーカイブ済みでも失敗せず、何も変えない
    assert main(["archive", str(review.id), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["changed"] is False
    assert main(["unarchive", str(review.id), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {
        "id": str(review.id),
        "archived": False,
        "changed": True,
    }
    assert not is_archived(review.id)
    assert main(["unarchive", str(review.id), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["changed"] is False


def test_list_hides_archived_and_archived_flag_shows_them(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = create_feature_review(project, ["src"], "old")
    create_feature_review(project, ["src"], "fresh")
    assert main(["archive", str(old.id)]) == EXIT_OK
    capsys.readouterr()
    assert main(["list", "--json"]) == EXIT_OK
    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == ["local/shop:fresh"]
    assert main(["list", "--archived", "--json"]) == EXIT_OK
    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == [str(old.id)]
    assert len(list_reviews(ARCHIVED_INCLUDE)) == 2
    assert [r.id for r in list_reviews(ARCHIVED_ONLY)] == [old.id]


def test_full_id_resolves_for_archived_review(project: Path) -> None:
    review = create_feature_review(project, ["src"], "old")
    assert main(["archive", str(review.id)]) == EXIT_OK
    assert resolve_review(str(review.id)) == review.id
    assert main(["show", str(review.id), "overview", "--json"]) == EXIT_OK


def test_short_id_falls_back_to_archived_when_nothing_else_matches(project: Path) -> None:
    review = create_feature_review(project, ["src"], "old")
    assert main(["archive", str(review.id)]) == EXIT_OK
    assert resolve_review("shop:old") == review.id


def test_refresh_keeps_marker(project: Path) -> None:
    review = create_feature_review(project, ["src"], "old")
    assert main(["archive", str(review.id)]) == EXIT_OK
    marker = review.id.directory() / ARCHIVE_MARKER_FILE
    before = marker.read_text(encoding="utf-8")
    (project / "src" / "a.py").write_text("# changed\n", encoding="utf-8")
    refresh_review(review.id)
    assert marker.read_text(encoding="utf-8") == before


def test_archive_does_not_mark_a_digits_only_feature_through_a_pull_request_id(
    project: Path,
) -> None:
    feature_id = save_review_of_kind(project, "7", ReviewKind.FEATURE)
    pull_request_id = ReviewId("local", "shop", "7", ReviewKind.PULL_REQUEST)
    assert main(["archive", str(pull_request_id)]) == EXIT_PROCESSING_FAILURE
    assert not is_archived(feature_id)
