"""保存物の書き込みが symlink を辿らず、推測できない一時ファイルを使うことを確かめる。"""

import json
import os
import re
import stat
import threading
from pathlib import Path

import pytest

from jigane.languages.approval import ApprovedContent, FileDigest
from jigane.languages.enabled import EnabledLanguage, load_enabled, save_enabled
from jigane.storage import atomic_write
from jigane.storage.atomic_write import write_text_atomic
from jigane.storage.locking import open_lock_file
from jigane.storage.real_directory import require_real_directory
from jigane.storage.symlink_guard import StoredFileSymlinkError


def test_symlink_destination_is_rejected_and_target_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("original", encoding="utf-8")
    link = tmp_path / "findings.json"
    link.symlink_to(target)
    with pytest.raises(StoredFileSymlinkError):
        write_text_atomic(link, "new")
    assert target.read_text(encoding="utf-8") == "original"
    assert link.is_symlink()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["findings.json", "outside.txt"]


def test_temporary_names_are_unpredictable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names: list[str] = []
    real_replace = Path.replace

    def record(self: Path, target: Path) -> Path:
        names.append(self.name)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", record)
    path = tmp_path / "findings.json"
    write_text_atomic(path, "a")
    write_text_atomic(path, "b")
    assert len(set(names)) == 2
    legacy = f".findings.json.{os.getpid()}.{threading.get_ident()}.tmp"
    assert legacy not in names
    assert all(name.startswith(".findings.json.") and name.endswith(".tmp") for name in names)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_planted_symlink_with_old_temporary_name_is_not_followed(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("original", encoding="utf-8")
    planted = tmp_path / f".findings.json.{os.getpid()}.{threading.get_ident()}.tmp"
    planted.symlink_to(victim)
    path = tmp_path / "findings.json"
    write_text_atomic(path, "new")
    assert victim.read_text(encoding="utf-8") == "original"
    assert path.read_text(encoding="utf-8") == "new"
    assert planted.is_symlink()


def _language() -> EnabledLanguage:
    content = ApprovedContent((FileDigest("a", "0" * 64),))
    return EnabledLanguage("kotlin", "jigane-kotlin", "1.0", content)


def test_save_enabled_uses_atomic_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    real = atomic_write.write_text_atomic

    def spy(path: Path, text: str) -> None:
        calls.append(path)
        real(path, text)

    monkeypatch.setattr("jigane.languages.enabled.write_text_atomic", spy)
    path = tmp_path / "languages.json"
    save_enabled(path, [_language()])
    assert calls == [path]
    assert load_enabled(path) == (_language(),)


def test_save_enabled_failure_keeps_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "languages.json"
    save_enabled(path, [_language()])
    before = path.read_text(encoding="utf-8")

    def crash(_self: Path, _target: Path) -> Path:
        raise RuntimeError("crash")

    monkeypatch.setattr(Path, "replace", crash)
    with pytest.raises(RuntimeError):
        save_enabled(path, [])
    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before)["enabled"]
    assert [p.name for p in tmp_path.iterdir()] == ["languages.json"]


def test_three_paths_raise_the_same_error(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    for action in (
        lambda: write_text_atomic(link, "x"),
        lambda: open_lock_file(link, os.O_RDWR),
        lambda: require_real_directory(link),
    ):
        with pytest.raises(StoredFileSymlinkError, match=re.escape(str(link))):
            action()


def test_save_enabled_rejects_symlink_destination_and_target_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_text("original", encoding="utf-8")
    link = tmp_path / "languages.json"
    link.symlink_to(target)
    with pytest.raises(StoredFileSymlinkError):
        save_enabled(link, [_language()])
    assert target.read_text(encoding="utf-8") == "original"
    assert link.is_symlink()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["languages.json", "outside.json"]
