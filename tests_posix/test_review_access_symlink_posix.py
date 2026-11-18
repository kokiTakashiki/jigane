from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from jigane.storage.review_access import (
    read_stored_file_if_present,
    read_stored_text,
    stored_file_exists,
)
from jigane.storage.store import CheckedReviewId
from jigane.storage.symlink_guard import StoredFileSymlinkError


def _review_id(directory: Path) -> CheckedReviewId:
    return cast("CheckedReviewId", SimpleNamespace(directory=lambda: directory))


def test_symlinked_file_is_refused_without_following(tmp_path: Path) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("secret", encoding="utf-8")
    directory = tmp_path / "review"
    directory.mkdir()
    (directory / "f.txt").symlink_to(target)
    with pytest.raises(StoredFileSymlinkError):
        read_stored_text(_review_id(directory), "f.txt")
    # 存在の確認は送出せず、symlink を無いものとして扱う
    assert not stored_file_exists(_review_id(directory), "f.txt")


def test_broken_symlink_is_refused_for_read_and_absent_for_exists(tmp_path: Path) -> None:
    (tmp_path / "f.txt").symlink_to(tmp_path / "missing.txt")
    with pytest.raises(StoredFileSymlinkError):
        read_stored_text(_review_id(tmp_path), "f.txt")
    assert not stored_file_exists(_review_id(tmp_path), "f.txt")


def test_if_present_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("secret", encoding="utf-8")
    directory = tmp_path / "review"
    directory.mkdir()
    (directory / "f.txt").symlink_to(target)
    with pytest.raises(StoredFileSymlinkError):
        read_stored_file_if_present(directory, "f.txt")


def test_if_present_refuses_broken_symlink(tmp_path: Path) -> None:
    (tmp_path / "f.txt").symlink_to(tmp_path / "missing.txt")
    with pytest.raises(StoredFileSymlinkError):
        read_stored_file_if_present(tmp_path, "f.txt")
