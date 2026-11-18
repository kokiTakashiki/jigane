from pathlib import Path
from types import SimpleNamespace
from typing import cast

from jigane.storage.review_access import (
    read_stored_file_if_present,
    read_stored_text,
    stored_file_exists,
)
from jigane.storage.store import CheckedReviewId


def _review_id(directory: Path) -> CheckedReviewId:
    return cast("CheckedReviewId", SimpleNamespace(directory=lambda: directory))


def test_regular_file_is_read(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("本文", encoding="utf-8")
    assert read_stored_text(_review_id(tmp_path), "f.txt") == "本文"
    assert stored_file_exists(_review_id(tmp_path), "f.txt")


def test_missing_file_is_none_and_absent(tmp_path: Path) -> None:
    assert read_stored_text(_review_id(tmp_path), "f.txt") is None
    assert not stored_file_exists(_review_id(tmp_path), "f.txt")


def test_directory_is_not_a_stored_file(tmp_path: Path) -> None:
    (tmp_path / "f.txt").mkdir()
    assert read_stored_text(_review_id(tmp_path), "f.txt") is None
    assert not stored_file_exists(_review_id(tmp_path), "f.txt")


def test_if_present_is_none_when_missing(tmp_path: Path) -> None:
    assert read_stored_file_if_present(tmp_path, "f.txt") is None


def test_if_present_reads_regular_file(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("本文", encoding="utf-8")
    assert read_stored_file_if_present(tmp_path, "f.txt") == "本文"
