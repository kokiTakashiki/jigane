import json
import shutil
from pathlib import Path

import pytest
from pr_support import commit_all, git

from jigane import commands
from jigane.commands.subcommands.refresh import refresh_review
from jigane.design.review_documents import create_design_doc_review
from jigane.reviews.lifecycle import (
    DesignDocOrigin,
    FeatureOrigin,
    ReviewError,
    _origin_from_stored,
    list_reviews,
    load_review,
)
from jigane.storage.store import ReviewId, ReviewKind, check_new_key


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "other.py").write_text("X = 1\n", encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "shop-design.md").write_text(
        "# 題\n\n## 1. 序論\n\n`Cart` を持つ。\n", encoding="utf-8"
    )
    commit_all(root, "add design")
    return root


def test_separator_does_not_collide_with_other_kinds() -> None:
    assert ReviewKind.DESIGN_DOC.separator == "+"
    assert len({kind.separator for kind in ReviewKind}) == len(ReviewKind)
    parsed = ReviewId.parse("octo/hello+shop")
    assert parsed.kind is ReviewKind.DESIGN_DOC
    assert str(parsed) == "octo/hello+shop"
    assert ReviewId.parse_short("hello+shop") == ("hello", ReviewKind.DESIGN_DOC, "shop")
    check_new_key(ReviewKind.DESIGN_DOC, "123")


def test_default_selection_is_all_recommended_and_name_from_file(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/shop-design.md")
    assert str(review.id) == "octo/hello+shop-design"
    origin = review.origin
    assert isinstance(origin, DesignDocOrigin)
    assert origin.document_path == "docs/shop-design.md"
    assert origin.selection == ("cart.py",)
    assert origin.code_version == git(repo, "rev-parse", "HEAD")
    assert review.paths == ["cart.py"]
    assert review.kind is ReviewKind.DESIGN_DOC
    assert load_review(review.id).origin == origin


def test_select_overrides_recommendation_and_name(repo: Path) -> None:
    review = create_design_doc_review(
        repo, "docs/shop-design.md", "mine", ["other.py", "cart.py", "other.py"]
    )
    assert str(review.id) == "octo/hello+mine"
    assert isinstance(review.origin, DesignDocOrigin)
    assert review.origin.selection == ("other.py", "cart.py")
    assert review.warnings == ["選んだファイルが新しい推しに無い: other.py"]


def test_select_rejects_missing_file_and_outside(repo: Path) -> None:
    with pytest.raises(ReviewError, match="選んだファイルがありません"):
        create_design_doc_review(repo, "docs/shop-design.md", None, ["ghost.py"])
    with pytest.raises(ReviewError, match="リポジトリの外"):
        create_design_doc_review(repo, "docs/shop-design.md", None, ["../outside.py"])
    with pytest.raises(ReviewError, match="設計書がありません"):
        create_design_doc_review(repo, "docs/none.md")


def test_refresh_updates_versions_and_warns_without_changing_selection(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/shop-design.md")
    assert isinstance(review.origin, DesignDocOrigin)
    unchanged = refresh_review(review.id)
    assert not unchanged.updated
    (repo / "docs" / "shop-design.md").write_text(
        "# 題\n\n## 1. 序論\n\n`Other` を持つ。\n", encoding="utf-8"
    )
    (repo / "other.py").write_text("class Other:\n    pass\n", encoding="utf-8")
    commit_all(repo, "change design")
    refreshed = refresh_review(review.id)
    assert refreshed.updated
    origin = refreshed.review.origin
    assert isinstance(origin, DesignDocOrigin)
    assert origin.document_version != review.origin.document_version
    assert origin.code_version == git(repo, "rev-parse", "HEAD")
    assert origin.selection == ("cart.py",)
    assert refreshed.review.warnings == ["選んだファイルが新しい推しに無い: cart.py"]


def test_refresh_warns_when_selected_file_disappears(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/shop-design.md")
    (repo / "cart.py").unlink()
    commit_all(repo, "remove cart")
    refreshed = refresh_review(review.id)
    assert refreshed.review.paths == []
    assert "選んだファイルがいまのコードにありません: cart.py" in refreshed.review.warnings


def test_origin_round_trip_through_stored_fields(repo: Path) -> None:
    review = create_design_doc_review(repo, "docs/shop-design.md")
    stored = review.stored_fields()
    assert stored["targets"] is None
    assert stored["diff_source"] is None
    assert set(stored["design_doc"]) == {
        "document_path",
        "document_version",
        "code_version",
        "selection",
    }
    restored = DesignDocOrigin.from_dict(json.loads(json.dumps(stored["design_doc"])))
    assert (
        _origin_from_stored(
            ReviewKind.DESIGN_DOC, None, None, None, None, design_doc=stored["design_doc"]
        )
        == review.origin
    )
    assert restored == review.origin


def test_unassemblable_combinations_are_rejected() -> None:
    origin = DesignDocOrigin("d.md", "v", "c", ()).to_dict()
    with pytest.raises(ReviewError, match="機能の対象"):
        _origin_from_stored(ReviewKind.DESIGN_DOC, None, None, None, ["a"], design_doc=origin)
    with pytest.raises(ReviewError, match="設計書の情報がありません"):
        _origin_from_stored(ReviewKind.DESIGN_DOC, None, None, None, None, design_doc=None)
    assert _origin_from_stored(
        ReviewKind.FEATURE, None, None, None, None, design_doc=None
    ) == FeatureOrigin(None)
    with pytest.raises(TypeError):
        DesignDocOrigin.from_dict(
            {"document_path": "d", "document_version": "v", "code_version": "c", "selection": "x"}
        )


def test_cli_new_list_show(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert commands.main(["new", "design-doc", "docs/shop-design.md", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["kind"] == "design_doc"
    assert created["design_doc"]["selection"] == ["cart.py"]
    assert [r.id.kind for r in list_reviews()] == [ReviewKind.DESIGN_DOC]

    assert commands.main(["list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["kind"] == "design_doc"
    assert rows[0]["design_doc"]["document_path"] == "docs/shop-design.md"
    assert rows[0]["title"] is None

    assert commands.main(["list"]) == 0
    assert "設計書: docs/shop-design.md" in capsys.readouterr().out

    assert commands.main(["show", "hello+shop-design"]) == 0
    shown = capsys.readouterr().out
    assert "設計書: docs/shop-design.md（版 " in shown
    assert "    cart.py" in shown

    named = ["new", "design-doc", "docs/shop-design.md", "--name", "b", "--select", "other.py"]
    assert commands.main(named) == 0
    assert "レビューを作りました: octo/hello+b" in capsys.readouterr().out


def test_help_describes_the_purpose(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        commands.main(["new", "--help"])
    assert "設計書をレビューします。実装の前に方針の綻びを問います" in capsys.readouterr().out
