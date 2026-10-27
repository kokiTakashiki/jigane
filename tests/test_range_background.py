"""範囲のレビューを通して、背景の過去の変更が中身のある値になることを確かめる。"""

from pathlib import Path

import pytest
from pr_support import commit_all, git
from store_format_support import check_upgrades

from jigane.pull_request_context.background import (
    _UPGRADES,
    BACKGROUND_FILE,
    BACKGROUND_FORMAT,
    PAST_PULL_REQUEST_LIMIT,
    PastChange,
    PastCommit,
    PastPullRequest,
)
from jigane.reviews.lifecycle import (
    RangeRefs,
    ReviewError,
    create_range_review,
    default_range_name,
    load_review,
    split_range,
)


def _history_repo(tmp_path: Path, *, extra_plain_commits: int = 0) -> Path:
    """番号つきのマージ、番号つきの squash、番号の無い通常のコミットが edit.py に触れた木。"""
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/octo/hello.git")
    (root / "edit.py").write_text("v = 0\n", encoding="utf-8")
    (root / "other.py").write_text("o = 0\n", encoding="utf-8")
    commit_all(root, "init")
    (root / "edit.py").write_text("v = 1\n", encoding="utf-8")
    commit_all(root, "direct push")
    git(root, "switch", "-c", "side")
    (root / "edit.py").write_text("v = 2\n", encoding="utf-8")
    commit_all(root, "side work")
    git(root, "switch", "main")
    git(
        root,
        "merge",
        "--no-ff",
        "side",
        "-m",
        "Merge pull request #12 from octo/side",
        "-m",
        "Fix v",
    )
    (root / "edit.py").write_text("v = 3\n", encoding="utf-8")
    (root / "other.py").write_text("o = 1\n", encoding="utf-8")
    commit_all(root, "Squash style change (#13)")
    for index in range(extra_plain_commits):
        (root / "edit.py").write_text(f"v = {10 + index}\n", encoding="utf-8")
        commit_all(root, f"plain {index}")
    git(root, "switch", "-c", "feature")
    (root / "edit.py").write_text("v = 99\n", encoding="utf-8")
    commit_all(root, "feature change")
    return root


def _past_changes(
    root: Path, range_text: str = "main..feature", name: str = "mine"
) -> tuple[PastChange, ...]:
    review = create_range_review(root, range_text, name)
    assert review.background is not None
    return review.background.past_changes


def test_range_review_background_carries_numbered_and_plain_changes(tmp_path: Path) -> None:
    root = _history_repo(tmp_path)
    found = _past_changes(root)
    pull_requests = {c.number: c for c in found if isinstance(c, PastPullRequest)}
    commits = {c.title: c for c in found if isinstance(c, PastCommit)}
    # マージのコミットの題は本文の最初の行、squash の題は括弧の番号を除いた件名
    assert {number: c.title for number, c in pull_requests.items()} == {
        12: "Fix v",
        13: "Squash style change",
    }
    # 範囲のファイル (edit.py) に触れたものだけが files に入り、other.py は入らない
    assert [c.files for c in pull_requests.values()] == [("edit.py",), ("edit.py",)]
    assert set(commits) == {"init", "direct push", "side work"}
    for commit in commits.values():
        assert len(commit.sha) == 40
        assert commit.files == ("edit.py",)
    # 番号つきのマージのコミットが、番号の無い通常のコミットとして重複して入らない
    assert len(found) == 5


def test_range_review_background_is_limited_to_the_cap(tmp_path: Path) -> None:
    root = _history_repo(tmp_path, extra_plain_commits=4)
    found = _past_changes(root)
    assert len(found) == PAST_PULL_REQUEST_LIMIT == 5


def test_range_review_background_survives_save_and_load(tmp_path: Path) -> None:
    root = _history_repo(tmp_path)
    created = create_range_review(root, "main..feature", "mine")
    loaded = load_review(created.id)
    assert loaded.background is not None
    assert created.background is not None
    assert loaded.background.past_changes == created.background.past_changes
    assert len(loaded.background.past_changes) == 5


def test_range_review_without_name_uses_the_head_ref(tmp_path: Path) -> None:
    root = _history_repo(tmp_path)
    review = create_range_review(root, "main..feature")
    assert review.id.key == "feature"
    assert review.background is not None
    assert len(review.background.past_changes) == 5


def test_range_review_with_open_head_uses_the_commit_for_the_name(tmp_path: Path) -> None:
    root = _history_repo(tmp_path)
    review = create_range_review(root, "main..")
    assert review.id.key == git(root, "rev-parse", "HEAD")[:8]
    assert review.background is not None
    assert len(review.background.past_changes) == 5


def test_three_dot_range_is_rejected(tmp_path: Path) -> None:
    root = _history_repo(tmp_path)
    with pytest.raises(ReviewError, match=r"<base>\.\.<head> の形"):
        create_range_review(root, "main...feature", "mine")


@pytest.mark.parametrize(
    ("range_text", "expected"),
    [("main..feature", RangeRefs("main", "feature")), ("main..", RangeRefs("main", "HEAD"))],
)
def test_split_range(range_text: str, expected: RangeRefs) -> None:
    assert split_range(range_text) == expected


@pytest.mark.parametrize("range_text", ["main", "..feature", "main...feature"])
def test_split_range_rejects_other_shapes(range_text: str) -> None:
    with pytest.raises(ReviewError):
        split_range(range_text)


@pytest.mark.parametrize(
    ("head", "expected"),
    [("feature", "feature"), ("feat/x", "feat-x"), ("HEAD", "abcdef12"), ("///", "abcdef12")],
)
def test_default_range_name(head: str, expected: str) -> None:
    assert default_range_name(head, "abcdef1234567890") == expected


def test_upgrade_table_has_every_step() -> None:
    check_upgrades(BACKGROUND_FILE, BACKGROUND_FORMAT, _UPGRADES)
