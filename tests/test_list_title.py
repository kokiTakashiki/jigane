import json
import shutil
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import create_feature_review
from jigane.storage.store import ReviewId, ReviewKind


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
    created = create_feature_review(project, ["src"], "payment")
    diff_directory = ReviewId(
        created.id.owner, created.id.repo, "5", ReviewKind.PULL_REQUEST
    ).directory()
    shutil.copytree(created.id.directory(), diff_directory)
    meta_path = diff_directory / "review.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = f"{created.id.owner}/{created.id.repo}#5"
    meta["mode"] = "diff"
    # 変更前の木の値を PR の値に同居させる版 1 の形で書くので、版を持たない記録にする。
    # 版 1 の記録は警告の欄を必ず持つ
    del meta["format"]
    meta["warnings"] = []
    meta["pull_request"] = {
        "url": "https://github.com/octo/shop/pull/5",
        "number": 5,
        "title": "Add payment retry",
        "base_sha": "b",
        "head_sha": "h",
        "merge_base": "m",
        "linked_issues": [],
        "base_tree": str(project),
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert main(["list", "--json"]) == EXIT_OK
    titles = {row["id"]: row["title"] for row in json.loads(capsys.readouterr().out)}
    assert titles == {"local/shop#5": "Add payment retry", "local/shop:payment": None}
