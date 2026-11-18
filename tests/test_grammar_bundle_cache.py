from pathlib import Path
from types import ModuleType

import pytest
import tree_sitter_language_pack as pack
from pr_support import git
from script_loader import import_script

from jigane.languages import grammars

BUNDLE = f"macos-arm64-{'a' * 64}.tar.zst"


def load_script() -> ModuleType:
    return import_script("grammar_bundle_cache")


def test_copy_bundles_copies_only_bundles_missing_from_the_destination(tmp_path: Path) -> None:
    module = load_script()
    source = tmp_path / "source"
    source.mkdir()
    (source / BUNDLE).write_bytes(b"bundle")
    kept = f"linux-x86_64-{'b' * 64}.tar.zst"
    (source / kept).write_bytes(b"new")
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    (source / "macos-arm64-short.tar.zst").write_bytes(b"x")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / kept).write_bytes(b"old")

    copied = module.copy_bundles(source, destination)

    assert copied == [BUNDLE]
    assert (destination / BUNDLE).read_bytes() == b"bundle"
    assert (destination / kept).read_bytes() == b"old"
    assert sorted(path.name for path in destination.iterdir()) == sorted([BUNDLE, kept])


def test_copy_bundles_from_a_missing_source_copies_nothing(tmp_path: Path) -> None:
    module = load_script()

    assert module.copy_bundles(tmp_path / "missing", tmp_path / "destination") == []
    assert not (tmp_path / "destination").exists()


def test_seed_then_save_moves_bundles_between_the_shared_and_environment_places(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script()
    shared = tmp_path / "shared"
    environment = tmp_path / "environment"
    monkeypatch.setattr(module, "shared_dir", lambda: shared)
    monkeypatch.setattr(module, "environment_dir", lambda: environment)
    shared.mkdir()
    (shared / BUNDLE).write_bytes(b"bundle")

    assert module.main(["seed"]) == 0
    fetched = f"linux-x86_64-{'c' * 64}.tar.zst"
    (environment / fetched).write_bytes(b"fetched")
    assert module.main(["save"]) == 0

    assert (environment / BUNDLE).read_bytes() == b"bundle"
    assert (shared / fetched).read_bytes() == b"fetched"
    assert capsys.readouterr().out.splitlines() == [f"seed: {BUNDLE}", f"save: {fetched}"]


def test_shared_dir_is_under_the_git_common_directory(tmp_path: Path) -> None:
    module = load_script()
    repository = tmp_path / "work"
    repository.mkdir()
    git(repository, "init", "-b", "main")

    shared = module.shared_dir(cwd=repository)

    assert shared.parent == (repository / ".git").resolve() / "jigane-grammar-bundles"


def test_seed_outside_a_git_repository_warns_and_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    assert module.main(["seed"]) == 0
    assert "使い回しを飛ばす" in capsys.readouterr().err


def test_the_environment_dir_is_beside_the_packs_library_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # パックは束を、文法の共有ライブラリの置き場と同じ親の bundles に置く
    module = load_script()
    monkeypatch.setenv(grammars.CACHE_ENV, str(tmp_path))
    monkeypatch.setattr(module, "cache_root", lambda: tmp_path)

    assert module.environment_dir() == Path(pack.cache_dir()).parent / "bundles"
