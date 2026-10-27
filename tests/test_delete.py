import contextlib
import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from review_fakes import save_review_of_kind

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands.delete import remove_review_directory
from jigane.reviews.lifecycle import ReviewError, create_feature_review, list_reviews, load_review
from jigane.storage.store import LOCK_FILE, ReviewId, ReviewKind, data_home, review_lock


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_delete_removes_only_that_review(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doomed = create_feature_review(project, ["src"], "doomed")
    sibling = create_feature_review(project, ["src"], "sibling")
    assert main(["delete", str(doomed.id), "--yes", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {"deleted": str(doomed.id)}
    assert not doomed.id.directory().exists()
    assert (sibling.id.directory() / "review.json").is_file()


def test_delete_removes_a_range_review_and_keeps_its_namesake_feature(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    feature = create_feature_review(project, ["src"], "v1")
    range_id = save_review_of_kind(project, "v1", ReviewKind.RANGE)
    assert range_id.directory().name == "@v1"
    assert main(["delete", str(range_id), "--yes", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {"deleted": str(range_id)}
    assert not range_id.directory().exists()
    assert (feature.id.directory() / "review.json").is_file()
    assert [review.id for review in list_reviews()] == [feature.id]


def test_delete_removes_empty_parents_but_not_home(project: Path) -> None:
    only = create_feature_review(project, ["src"], "only")
    assert main(["delete", str(only.id), "--yes"]) == EXIT_OK
    assert data_home().is_dir()
    assert not (data_home() / only.id.owner).exists()


def test_delete_text_output(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert main(["delete", str(review.id), "--yes"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == f"削除しました: {review.id}"


@pytest.mark.usefixtures("project")
def test_guard_refuses_a_path_outside_home(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    owner = data_home() / "o"
    owner.mkdir(parents=True)
    (owner / "r").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="データの場所の外"):
        remove_review_directory(ReviewId("o", "r", "k", ReviewKind.FEATURE))
    assert (outside / "keep.txt").is_file()


@pytest.mark.usefixtures("project")
def test_delete_unknown_review_fails_with_kind_review(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["delete", "local/shop:none", "--json"]) == EXIT_PROCESSING_FAILURE
    assert json.loads(capsys.readouterr().out)["error"]["kind"] == "review"


def test_lock_file_is_removed_only_after_the_lock_is_released(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = create_feature_review(project, ["src"], "locked")
    directory = review.id.directory()
    held = False
    real_lock = review_lock
    real_unlink = os.unlink

    @contextlib.contextmanager
    def tracking_lock(path: Path) -> Iterator[None]:
        nonlocal held
        with real_lock(path):
            held = True
            try:
                yield
            finally:
                held = False

    def unlink_like_windows(path: str | os.PathLike[str], *, dir_fd: int | None = None) -> None:
        if held and Path(path).name == LOCK_FILE:
            raise PermissionError("開いているファイルは消せない")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr("jigane.commands.subcommands.delete.review_lock", tracking_lock)
    monkeypatch.setattr(os, "unlink", unlink_like_windows)
    monkeypatch.setattr(os, "remove", unlink_like_windows)
    remove_review_directory(review.id)
    assert not directory.exists()


def test_delete_without_yes_refuses_on_a_non_tty(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "keep")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main(["delete", str(review.id)]) == EXIT_PROCESSING_FAILURE
    assert "確認できません。--yes を付けてください" in capsys.readouterr().err
    assert review.id.directory().exists()


def test_delete_on_a_tty_keeps_the_review_when_answered_no(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = create_feature_review(project, ["src"], "keep")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["delete", str(review.id)]) == EXIT_PROCESSING_FAILURE
    assert review.id.directory().exists()


def test_delete_on_a_tty_deletes_when_answered_yes(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = create_feature_review(project, ["src"], "gone")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert main(["delete", str(review.id)]) == EXIT_OK
    assert not review.id.directory().exists()


def fail_on_nth_rename(monkeypatch: pytest.MonkeyPatch, fail_at: int) -> None:
    real_rename = Path.rename
    calls = 0

    def flaky_rename(self: Path, target: str | os.PathLike[str]) -> Path:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("改名に失敗した")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)


def test_a_failure_in_the_middle_leaves_the_review_whole(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = create_feature_review(project, ["src"], "whole")
    directory = review.id.directory()
    before = sorted(entry.name for entry in directory.iterdir() if entry.name != LOCK_FILE)
    assert len(before) > 3
    with monkeypatch.context() as patch:
        fail_on_nth_rename(patch, fail_at=3)
        with pytest.raises(OSError, match="改名に失敗した"):
            remove_review_directory(review.id)
    after = sorted(entry.name for entry in directory.iterdir() if entry.name != LOCK_FILE)
    assert after == before
    assert list((data_home() / ".trash").iterdir()) == []
    remove_review_directory(review.id)
    assert not directory.exists()


def test_a_leftover_tombstone_is_not_listed_as_a_review(project: Path) -> None:
    review = create_feature_review(project, ["src"], "listed")
    tombstone = data_home() / ".trash" / "leftover" / "a" / "b"
    tombstone.mkdir(parents=True)
    source = review.id.directory() / "review.json"
    (tombstone / "review.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    assert [item.id for item in list_reviews()] == [review.id]


def test_a_successful_delete_leaves_no_tombstone(project: Path) -> None:
    review = create_feature_review(project, ["src"], "clean")
    remove_review_directory(review.id)
    assert list((data_home() / ".trash").iterdir()) == []


def test_delete_does_not_remove_a_digits_only_feature_through_a_pull_request_id(
    project: Path,
) -> None:
    feature_id = save_review_of_kind(project, "7", ReviewKind.FEATURE)
    pull_request_id = ReviewId("local", "shop", "7", ReviewKind.PULL_REQUEST)
    assert main(["delete", str(pull_request_id), "--yes"]) == EXIT_PROCESSING_FAILURE
    assert (feature_id.directory() / "review.json").is_file()


@pytest.mark.parametrize(
    "broken",
    ["{not json", json.dumps({"created_at": "t"}), json.dumps({"id": "not-an-id"})],
    ids=["syntax", "missing-id", "malformed-id"],
)
def test_delete_removes_a_broken_record_by_its_full_id(project: Path, broken: str) -> None:
    review_id = save_review_of_kind(project, "doomed", ReviewKind.FEATURE)
    (review_id.directory() / "review.json").write_text(broken, encoding="utf-8")
    with pytest.raises(ReviewError):
        load_review(review_id)
    assert main(["delete", str(review_id), "--yes"]) == EXIT_OK
    assert not review_id.directory().exists()
