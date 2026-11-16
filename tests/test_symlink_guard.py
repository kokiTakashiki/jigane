import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from jigane.storage.symlink_guard import StoredFileSymlinkError, is_link_like

posix_only = pytest.mark.skipif(os.name == "nt", reason="symlink の作成は POSIX だけ試す")


def test_missing_path_is_not_link(tmp_path: Path) -> None:
    assert not is_link_like(tmp_path / "missing")


def test_regular_file_and_directory_are_not_link(tmp_path: Path) -> None:
    file = tmp_path / "f"
    file.write_text("x", encoding="utf-8")
    assert not is_link_like(file)
    assert not is_link_like(tmp_path)


@posix_only
def test_symlink_is_link(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "missing")
    assert is_link_like(link)


def _reparse_status(attributes: int) -> SimpleNamespace:
    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=attributes)


@pytest.mark.parametrize(
    ("platform", "attributes", "expected"),
    [
        ("win32", stat.FILE_ATTRIBUTE_REPARSE_POINT, True),
        ("win32", 0, False),
        ("linux", stat.FILE_ATTRIBUTE_REPARSE_POINT, False),
    ],
)
def test_reparse_point_is_link_only_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform: str,
    attributes: int,
    *,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        "jigane.storage.symlink_guard.os",
        SimpleNamespace(lstat=lambda _path: _reparse_status(attributes)),
    )
    monkeypatch.setattr("jigane.storage.symlink_guard.sys", SimpleNamespace(platform=platform))
    assert is_link_like(tmp_path) is expected


def test_error_message_names_the_path(tmp_path: Path) -> None:
    error = StoredFileSymlinkError(tmp_path / "x")
    assert str(tmp_path / "x") in str(error)
    assert "symlink" in str(error)
