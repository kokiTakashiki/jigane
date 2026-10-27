import shutil
from pathlib import Path

import pytest

from jigane.pull_request_context import fetching as pull_request
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import (
    STAGING_PREFIX,
    ReviewError,
    create_feature_review,
    create_range_review,
    list_reviews,
)
from jigane.storage.review_error import ReviewExistsError
from jigane.storage.store import ReviewId, ReviewKind


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    return root


def _boom(*_arguments: object, **_keywords: object) -> None:
    raise RuntimeError("途中で失敗")


def _repository_directory(tmp_path: Path) -> Path:
    return tmp_path / "home" / "octo" / "hello"


def test_failure_while_diffing_leaves_nothing(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(review_module, "changed_file_pairs", _boom)
    with pytest.raises(RuntimeError, match="途中で失敗"):
        create_range_review(repo, "main..feature", "mine")
    assert list(_repository_directory(tmp_path).iterdir()) == []


def test_failure_while_extracting_second_tree_leaves_nothing(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = pull_request.extract_tree
    calls: list[Path] = []

    def extract_then_fail(source_dir: Path, commit: str, destination: Path) -> None:
        calls.append(destination)
        if len(calls) == 2:
            raise RuntimeError("途中で失敗")
        original(source_dir, commit, destination)

    monkeypatch.setattr(review_module, "extract_tree", extract_then_fail)
    with pytest.raises(RuntimeError, match="途中で失敗"):
        create_range_review(repo, "main..feature", "mine")
    assert len(calls) == 2
    assert list(_repository_directory(tmp_path).iterdir()) == []


def test_failure_while_saving_feature_review_leaves_nothing(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(review_module, "update_gates", _boom)
    with pytest.raises(RuntimeError, match="途中で失敗"):
        create_feature_review(repo, ["edit.py"], "feat")
    assert list(_repository_directory(tmp_path).iterdir()) == []


def test_success_puts_everything_in_place_and_stores_final_paths(
    repo: Path, tmp_path: Path
) -> None:
    review = create_range_review(repo, "main..feature", "mine")
    directory = review.id.directory()
    assert [path.name for path in _repository_directory(tmp_path).iterdir()] == ["@mine"]
    assert (directory / "review.json").is_file()
    assert (directory / "trees/head/new.py").is_file()
    assert review.base_tree_path == directory / "trees/base"
    assert review.repository_root == str(directory / "trees/head")
    for stored in directory.rglob("*.json"):
        assert STAGING_PREFIX not in stored.read_text(encoding="utf-8")


def test_residue_without_review_file_is_replaced(repo: Path) -> None:
    residue = ReviewId("octo", "hello", "mine", ReviewKind.RANGE).directory()
    (residue / "trees/base").mkdir(parents=True)
    (residue / "trees/base/stale.py").write_text("stale\n", encoding="utf-8")
    (residue / "changes.diff").write_text("stale\n", encoding="utf-8")
    review = create_range_review(repo, "main..feature", "mine")
    assert not (residue / "trees/base/stale.py").exists()
    assert (review.id.directory() / "review.json").is_file()


def test_existing_review_is_kept_and_rejected(repo: Path, tmp_path: Path) -> None:
    review = create_range_review(repo, "main..feature", "mine")
    before = (review.id.directory() / "review.json").read_text(encoding="utf-8")
    with pytest.raises(ReviewError, match="既にあります"):
        create_range_review(repo, "main..feature", "mine")
    assert (review.id.directory() / "review.json").read_text(encoding="utf-8") == before
    assert [path.name for path in _repository_directory(tmp_path).iterdir()] == ["@mine"]


def test_create_lost_to_a_concurrent_create_keeps_the_winner(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = review_module._install_staging
    winner: list[Path] = []

    def install_after_winner(staging: Path, final: Path, review_id: ReviewId) -> None:
        if not winner:
            # 先に同じ名前の作成が改名を済ませた状況
            winner.append(final)
            final.mkdir()
            (final / "review.json").write_text("winner", encoding="utf-8")
        original(staging, final, review_id)

    monkeypatch.setattr(review_module, "_install_staging", install_after_winner)
    with pytest.raises(ReviewError, match="既にあります"):
        create_range_review(repo, "main..feature", "mine")
    assert (winner[0] / "review.json").read_text(encoding="utf-8") == "winner"
    assert [path.name for path in _repository_directory(tmp_path).iterdir()] == ["@mine"]


def test_staging_directory_is_not_listed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_range_review(repo, "main..feature", "mine")
    seen: list[list[str]] = []
    original = review_module._install_staging

    def list_while_staged(staging: Path, final: Path, review_id: ReviewId) -> None:
        assert staging.name.startswith(STAGING_PREFIX)
        assert (staging / "review.json").is_file()
        seen.append([str(review.id) for review in list_reviews()])
        original(staging, final, review_id)

    monkeypatch.setattr(review_module, "_install_staging", list_while_staged)
    create_feature_review(repo, ["edit.py"], "feat")
    assert seen == [["octo/hello@mine"]]


def test_removal_refuses_paths_outside_the_review_directory(tmp_path: Path) -> None:
    review_id = ReviewId("octo", "hello", "mine", ReviewKind.RANGE)
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    sibling = home / "octo" / "hello" / "@other"
    sibling.mkdir(parents=True)
    for path, staging in ((home, False), (outside, False), (sibling, False), (sibling, True)):
        with pytest.raises(ReviewError):
            review_module._remove_directory_in_home(path, review_id, staging=staging)
        assert path.is_dir()


def test_range_review_over_existing_raises_review_exists_error(repo: Path) -> None:
    create_range_review(repo, "main..feature", "mine")
    with pytest.raises(ReviewExistsError) as raised:
        create_range_review(repo, "main..feature", "mine")
    assert str(raised.value.review_id) == "octo/hello@mine"
    assert "作り直すには refresh してください" in str(raised.value)


def test_range_create_lost_to_a_concurrent_create_raises_review_exists_error(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = review_module._install_staging

    def install_after_winner(staging: Path, final: Path, review_id: ReviewId) -> None:
        final.mkdir()
        (final / "review.json").write_text("winner", encoding="utf-8")
        original(staging, final, review_id)

    monkeypatch.setattr(review_module, "_install_staging", install_after_winner)
    with pytest.raises(ReviewExistsError) as raised:
        create_range_review(repo, "main..feature", "mine")
    assert str(raised.value.review_id) == "octo/hello@mine"


def test_pull_request_install_over_existing_raises_review_exists_error(tmp_path: Path) -> None:
    review_id = ReviewId("octo", "hello", "7", ReviewKind.PULL_REQUEST)
    final = tmp_path / "final"
    final.mkdir()
    (final / "review.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReviewExistsError) as raised:
        review_module._install_staging(tmp_path / "staging", final, review_id)
    assert raised.value.review_id == review_id
    assert "作者の修正は refresh で取り込んでください" in str(raised.value)
