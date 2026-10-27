"""設計書の識別子の区切り「+」が、CLI の引数と保存先で往復する。"""

import json
import shutil
from pathlib import Path

import pytest
from pr_support import commit_all

from jigane import commands
from jigane.design.review_documents import create_design_doc_review
from jigane.reviews.lifecycle import load_review, resolve_review
from jigane.storage.store import ReviewId, ReviewKind

IDENTIFIER = "octo/hello+shop"


def test_plus_round_trips_through_the_cli_and_the_directory_name(
    repo_template: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "shop.md").write_text(
        "# 題\n\n## 1. 序論\n\n`Cart` を持つ。\n", encoding="utf-8"
    )
    commit_all(root, "add design")
    review = create_design_doc_review(root, "docs/shop.md", "shop")
    assert str(review.id) == IDENTIFIER
    assert review.id.directory().name == "+shop"
    assert ReviewId.parse(IDENTIFIER) == review.id
    assert ReviewId.parse(IDENTIFIER).kind is ReviewKind.DESIGN_DOC
    assert load_review(review.id).id == review.id
    monkeypatch.chdir(root)
    assert resolve_review(IDENTIFIER) == review.id
    assert commands.main(["show", IDENTIFIER, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == IDENTIFIER
