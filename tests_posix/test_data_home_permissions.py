"""既にあるデータの置き場の根の権限の検査の試験。POSIX だけの置き場に置く。

Windows は ACL に任せ、mode と所有者を検査しない。
"""

import os
import stat
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.reviews.catalog import TRASH_DIRECTORY, read_review_at
from jigane.storage import store
from jigane.storage.locking import LOCK_FILE
from jigane.storage.review_creation import SaveTarget, _make_staging, prepare_save_target
from jigane.storage.store import CheckedReviewId, ReviewId, ReviewKind, check_review_id


def _use_root(monkeypatch: pytest.MonkeyPatch, home: Path, mode: int) -> None:
    home.mkdir()
    home.chmod(mode)
    monkeypatch.setenv("JIGANE_HOME", str(home))


@pytest.mark.parametrize("mode", [0o700, 0o750, 0o755])
def test_ensure_data_home_accepts_root_without_group_or_other_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: int
) -> None:
    home = tmp_path / "jigane"
    _use_root(monkeypatch, home, mode)
    assert store.ensure_data_home() == home
    assert stat.S_IMODE(home.stat().st_mode) == mode


@pytest.mark.parametrize("mode", [0o770, 0o777, 0o702])
def test_ensure_data_home_rejects_root_writable_by_group_or_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: int
) -> None:
    _use_root(monkeypatch, tmp_path / "jigane", mode)
    with pytest.raises(store.ReviewError, match="chmod 700"):
        store.ensure_data_home()


def test_ensure_data_home_rejects_root_owned_by_another_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_root(monkeypatch, tmp_path / "jigane", 0o700)
    # Windows の型検査は os.getuid を知らないので、実行ユーザーはファイルの所有者から取る
    other_uid = (tmp_path / "jigane").stat().st_uid + 1
    monkeypatch.setattr(os, "getuid", lambda: other_uid, raising=False)
    with pytest.raises(store.ReviewError, match="所有者が実行ユーザーと違います") as caught:
        store.ensure_data_home()
    assert "chmod" not in str(caught.value)


def test_ensure_data_home_checks_target_of_symlinked_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "real"
    _use_root(monkeypatch, target, 0o777)
    link = tmp_path / "link"
    link.symlink_to(target)
    monkeypatch.setenv("JIGANE_HOME", str(link))
    with pytest.raises(store.ReviewError):
        store.ensure_data_home()
    target.chmod(0o700)
    assert store.ensure_data_home() == link


def test_ensure_data_home_creates_missing_root_with_mode_0700(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "jigane"
    monkeypatch.setenv("JIGANE_HOME", str(home))
    previous = os.umask(0)
    try:
        store.ensure_data_home()
    finally:
        os.umask(previous)
    assert stat.S_IMODE(home.stat().st_mode) == 0o700


def test_ensure_data_home_creates_missing_ancestors_with_mode_0700_and_keeps_existing_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    existing.chmod(0o755)
    home = existing / "a" / "b" / "jigane"
    monkeypatch.setenv("JIGANE_HOME", str(home))
    previous = os.umask(0o022)
    try:
        store.ensure_data_home()
    finally:
        os.umask(previous)
    for created in (existing / "a", existing / "a" / "b", home):
        assert stat.S_IMODE(created.stat().st_mode) == 0o700
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755


def test_ensure_data_home_skips_check_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "jigane"
    _use_root(monkeypatch, home, 0o777)
    monkeypatch.setattr("sys.platform", "win32")
    assert store.ensure_data_home() == home


def test_review_directories_have_no_group_or_other_permission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "jigane"))
    previous = os.umask(0)
    try:
        final = tmp_path / "jigane" / "owner" / "repo" / "review"
        staging = _make_staging(final)
        target = SaveTarget(final, tmp_path / "jigane" / "owner" / "other" / "writing")
        prepare_save_target(target)
    finally:
        os.umask(previous)
    for directory in (final.parent, staging, target.writing):
        assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0


def test_delete_rejects_root_writable_by_group_or_other_without_removing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "jigane"
    _use_root(monkeypatch, home, 0o700)
    review_id = check_review_id(ReviewId("o", "r", "k", ReviewKind.FEATURE))
    directory = review_id.directory()
    directory.mkdir(parents=True)
    (directory / "review.json").write_text("{}", encoding="utf-8")
    home.chmod(0o777)
    assert main(["delete", "o/r:k", "--yes"]) == EXIT_PROCESSING_FAILURE
    assert "chmod 700" in capsys.readouterr().err
    assert (directory / "review.json").is_file()
    assert not (home / TRASH_DIRECTORY).exists()


def _existing_review() -> CheckedReviewId:
    review_id = check_review_id(ReviewId("o", "r", "k", ReviewKind.FEATURE))
    review_id.directory().mkdir(parents=True)
    return review_id


def test_write_command_stops_on_root_writable_by_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "jigane"
    _use_root(monkeypatch, home, 0o700)
    review_id = _existing_review()
    home.chmod(0o777)
    assert main(["archive", "o/r:k"]) == EXIT_PROCESSING_FAILURE
    assert "chmod 700" in capsys.readouterr().err
    assert not (review_id.directory() / LOCK_FILE).exists()


def test_read_command_is_not_blocked_by_root_writable_by_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "jigane"
    _use_root(monkeypatch, home, 0o700)
    _existing_review()
    home.chmod(0o777)
    assert main(["list", "--json"]) == EXIT_OK
    assert "chmod 700" not in capsys.readouterr().err


def test_write_command_reaches_its_handler_when_root_is_0700(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "jigane"
    _use_root(monkeypatch, home, 0o700)
    _existing_review()
    # レビューの中身が無いため副コマンドは失敗するが、権限の文面ではない
    main(["archive", "o/r:k"])
    assert "chmod 700" not in capsys.readouterr().err


def test_read_review_at_is_not_blocked_by_root_writable_by_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "jigane"
    _use_root(monkeypatch, home, 0o700)
    review_id = _existing_review()
    home.chmod(0o777)
    # 読み取りは根の検査を通らない。中身が無いための失敗は出るが、権限の文面ではない
    with pytest.raises(store.ReviewError) as caught:
        read_review_at(review_id.directory())
    assert "chmod 700" not in str(caught.value)
