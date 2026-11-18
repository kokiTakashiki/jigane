import json
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.catalog import (
    ARCHIVE_MARKER_FILE,
    ARCHIVED_INCLUDE,
    ARCHIVED_ONLY,
    is_archived,
    list_reviews,
)
from jigane.reviews.lifecycle import create_feature_review


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_symlinked_marker_is_not_archived_and_does_not_stop_listing(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "linked")
    (review.id.directory() / ARCHIVE_MARKER_FILE).symlink_to(project / "src" / "a.py")
    assert not is_archived(review.id)
    assert list_reviews(ARCHIVED_ONLY) == []
    assert [r.id for r in list_reviews(ARCHIVED_INCLUDE)] == [review.id]
    capsys.readouterr()
    assert main(["list", "--archived", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == []
    assert main(["list", "--json"]) == EXIT_OK
    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == [str(review.id)]
