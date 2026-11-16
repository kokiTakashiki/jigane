import json
from pathlib import Path

import pytest
from review_fakes import save_review_of_kind

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import create_feature_review
from jigane.storage.store import ReviewKind


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_list_json_carries_pull_request_title_and_null_for_feature_review(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "payment")
    review_id = save_review_of_kind(project, "5", ReviewKind.PULL_REQUEST)
    meta_path = review_id.directory() / "review.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["pull_request"]["title"] = "Add payment retry"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert main(["list", "--json"]) == EXIT_OK
    titles = {row["id"]: row["title"] for row in json.loads(capsys.readouterr().out)}
    assert titles == {"local/shop#5": "Add payment retry", "local/shop:payment": None}
