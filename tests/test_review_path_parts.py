from pathlib import Path

import pytest

from jigane.storage.store import (
    ReviewId,
    ReviewKind,
    _is_plain_part,
    check_new_review_id,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("JIGANE_HOME", str(path))
    return path


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "/",
        "a/b",
        "\\",
        "a\\b",
        "C:",
        "C:\\",
        "\\\\?\\C:",
        "\\\\server\\share",
    ],
)
def test_a_part_with_a_dot_separator_drive_or_root_is_not_plain(name: str) -> None:
    assert not _is_plain_part(name)


@pytest.mark.parametrize("name", ["octo", "@mine", "hello.world"])
def test_a_single_name_is_plain(name: str) -> None:
    assert _is_plain_part(name)


def test_new_review_id_under_a_dangling_link_to_the_outside_is_refused(
    home: Path, tmp_path: Path
) -> None:
    # 宛先がまだ無いので strict の解決は失敗する。まだ無い部分として後ろに付けると内側に見える
    (home / "octo").symlink_to(tmp_path / "outside", target_is_directory=True)

    with pytest.raises(ValueError, match="データの場所の外"):
        check_new_review_id(ReviewId("octo", "hello", "mine", ReviewKind.RANGE))


def test_new_review_id_whose_parents_do_not_exist_yet_is_inside_home(home: Path) -> None:
    checked = check_new_review_id(ReviewId("octo", "hello", "mine", ReviewKind.RANGE))

    assert checked.directory() == home / "octo" / "hello" / "@mine"
