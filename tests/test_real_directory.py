import os
from pathlib import Path

import pytest
from fakes import run_command_stub

from jigane.git import process
from jigane.pull_request_context import fetching, gh
from jigane.storage.real_directory import NotARealDirectoryError, require_real_directory
from jigane.storage.symlink_guard import StoredFileSymlinkError

posix_only = pytest.mark.skipif(os.name == "nt", reason="symlink の作成は POSIX だけ試す")


def test_missing_path_passes(tmp_path: Path) -> None:
    require_real_directory(tmp_path / "missing")


def test_real_directory_passes(tmp_path: Path) -> None:
    require_real_directory(tmp_path)


def test_file_is_rejected(tmp_path: Path) -> None:
    file = tmp_path / "f"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(NotARealDirectoryError, match="ディレクトリではありません"):
        require_real_directory(file)


@posix_only
def test_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(StoredFileSymlinkError, match="symlink"):
        require_real_directory(link)


def _forbid_clone(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []
    monkeypatch.setattr(process, "run_command", run_command_stub(lambda _args: ""))

    def forbidden_clone(_owner: str, _repo: str, _destination: str) -> None:
        called.append("clone")

    monkeypatch.setattr(gh, "clone_repository", forbidden_clone)
    return called


@posix_only
@pytest.mark.parametrize("linked", ["repositories", "owner", "repo"])
def test_ensure_cache_stops_on_symlinked_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, linked: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("JIGANE_HOME", str(home))
    called = _forbid_clone(monkeypatch)
    if linked == "repositories":
        (home / "repositories").symlink_to(outside)
    else:
        (home / "repositories").mkdir()
        if linked == "owner":
            (home / "repositories" / "o").symlink_to(outside)
        else:
            (home / "repositories" / "o").mkdir()
            (home / "repositories" / "o" / "r.git").symlink_to(outside)
    with pytest.raises(StoredFileSymlinkError):
        fetching.ensure_repository_cache("o", "r")
    assert called == []
    assert list(outside.iterdir()) == []


@posix_only
def test_ensure_cache_stops_when_level_is_swapped_after_mkdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("JIGANE_HOME", str(home))
    called = _forbid_clone(monkeypatch)
    owner_dir = home / "repositories" / "o"
    real_mkdir = Path.mkdir

    def swapping_mkdir(
        self: Path, mode: int = 0o777, *, parents: bool = False, exist_ok: bool = False
    ) -> None:
        real_mkdir(self, mode, parents=parents, exist_ok=exist_ok)
        if self == owner_dir and not owner_dir.is_symlink():
            owner_dir.rmdir()
            owner_dir.symlink_to(outside)

    monkeypatch.setattr(Path, "mkdir", swapping_mkdir)
    with pytest.raises(StoredFileSymlinkError):
        fetching.ensure_repository_cache("o", "r")
    assert called == []
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("plain_file", ["repositories", "owner"])
def test_ensure_cache_stops_on_regular_file_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plain_file: str
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    called = _forbid_clone(monkeypatch)
    if plain_file == "repositories":
        (tmp_path / "repositories").write_text("x", encoding="utf-8")
    else:
        (tmp_path / "repositories").mkdir()
        (tmp_path / "repositories" / "o").write_text("x", encoding="utf-8")
    with pytest.raises(NotARealDirectoryError):
        fetching.ensure_repository_cache("o", "r")
    assert called == []


def test_ensure_cache_with_real_directories_clones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    (tmp_path / "repositories" / "o").mkdir(parents=True)
    monkeypatch.setattr(process, "run_command", run_command_stub(lambda _args: ""))
    monkeypatch.setattr(
        gh,
        "clone_repository",
        lambda _owner, _repo, destination: Path(destination).mkdir(exist_ok=True),
    )
    expected = tmp_path / "repositories" / "o" / "r.git"
    assert fetching.ensure_repository_cache("o", "r") == expected
    assert fetching.ensure_repository_cache("o", "r") == expected
