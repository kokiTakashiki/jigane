"""SARIF を重ねたレビューの refresh を、PR のレビューと機能のレビューで確かめる。"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pr_support import URL, advance_head, commit_all
from sarif_support import log, result, run, write

from jigane.commands.subcommands.refresh import refresh_review
from jigane.reviews.lifecycle import (
    DeclaredInputs,
    ReviewInputs,
    create_feature_review,
    create_pull_request_review,
)
from jigane.static_analysis.intake import SarifRequest, read_static_findings, sarif_request
from jigane.storage.store import ReviewId

MESSAGE = "UNIQUE-REFRESH-MESSAGE-5512"
WARNING = "--sarif で渡し直"


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    return {**repo_template, "root": str(root)}


def _request(scratch: Path, root: Path, commit: str) -> SarifRequest | None:
    document = log(
        run(
            [result("edit.py", 2, MESSAGE, level="error")],
            versionControlProvenance=[{"revisionId": commit}],
        )
    )
    return sarif_request([write(scratch, "a.sarif", document)], None, root)


def _static_ids(review_id: ReviewId) -> set[str]:
    stored = json.loads((review_id.directory() / "map.json").read_text(encoding="utf-8"))
    return {finding for segment in stored["segments"] for finding in segment["static_finding_ids"]}


def _forget_static_in_map(review_id: ReviewId) -> None:
    # 分類の規則が変わる前に保存した地図を真似る。同じコミットでも地図が作り直される
    path = review_id.directory() / "map.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    for segment in stored["segments"]:
        segment["static_finding_ids"] = []
    path.write_text(json.dumps(stored), encoding="utf-8")


def test_pull_request_refresh_at_the_same_head_overlays_again(
    pr: dict[str, Any], tmp_path: Path
) -> None:
    sarif = _request(tmp_path, pr["origin"], pr["head"])
    review = create_pull_request_review(URL, ReviewInputs(sarif=sarif))
    assert _static_ids(review.id) == {"S1"}
    _forget_static_in_map(review.id)
    assert _static_ids(review.id) == set()
    refreshed = refresh_review(review.id)
    assert _static_ids(refreshed.review.id) == {"S1"}


def test_pull_request_refresh_after_a_new_head_warns_and_does_not_overlay(
    pr: dict[str, Any], tmp_path: Path
) -> None:
    analyzed = pr["head"]
    review = create_pull_request_review(
        URL, ReviewInputs(sarif=_request(tmp_path, pr["origin"], analyzed))
    )
    advance_head(pr)
    refreshed = refresh_review(review.id)
    assert refreshed.updated is True
    assert _static_ids(review.id) == set()
    assert any(WARNING in warning for warning in refreshed.review.warnings)
    stored = read_static_findings(review.id.directory())
    assert stored is not None
    assert stored.analyzed_commit == analyzed


def test_feature_refresh_at_the_same_head_overlays_again(
    repo: dict[str, str], tmp_path: Path
) -> None:
    root = Path(repo["root"])
    review = create_feature_review(
        root, ["edit.py"], "feat", DeclaredInputs(sarif=_request(tmp_path, root, repo["head"]))
    )
    assert _static_ids(review.id) == {"S1"}
    _forget_static_in_map(review.id)
    assert _static_ids(review.id) == set()
    refreshed = refresh_review(review.id)
    assert _static_ids(refreshed.review.id) == {"S1"}


def test_feature_refresh_after_a_new_head_warns_and_does_not_overlay(
    repo: dict[str, str], tmp_path: Path
) -> None:
    root = Path(repo["root"])
    review = create_feature_review(
        root, ["edit.py"], "feat", DeclaredInputs(sarif=_request(tmp_path, root, repo["head"]))
    )
    (root / "extra.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    commit_all(root, "add delta")
    refreshed = refresh_review(review.id)
    assert _static_ids(review.id) == set()
    assert any(WARNING in warning for warning in refreshed.review.warnings)
    stored = read_static_findings(review.id.directory())
    assert stored is not None
    assert stored.analyzed_commit == repo["head"]
