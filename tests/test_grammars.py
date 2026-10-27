import hashlib
import re
from pathlib import Path

import pytest
import tree_sitter_language_pack
from markdown_support import items

from jigane.languages import grammars
from jigane.languages.registry import adapters


def _fake_library(root: Path, grammar: str, content: bytes) -> Path:
    directory = grammars.library_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"libtree_sitter_{grammar}.so"
    path.write_bytes(content)
    return path


def _record(monkeypatch: pytest.MonkeyPatch, grammar: str, digest: str) -> None:
    monkeypatch.setattr(
        grammars, "_recorded", lambda: {grammars.platform_key(): {grammar: digest}}
    )


def test_adapter_grammars_are_all_pinned() -> None:
    assert {adapter.grammar for adapter in adapters()} == set(grammars.GRAMMARS)


def test_every_recorded_platform_covers_every_grammar() -> None:
    for hashes in grammars._recorded().values():
        assert set(hashes) == set(grammars.GRAMMARS)


def test_mismatched_hash_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_library(tmp_path, "python", b"tampered")
    _record(monkeypatch, "python", hashlib.sha256(b"genuine").hexdigest())
    with pytest.raises(grammars.GrammarError, match="make setup"):
        grammars.verify(tmp_path, "python")


def test_matching_hash_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_library(tmp_path, "python", b"genuine")
    _record(monkeypatch, "python", hashlib.sha256(b"genuine").hexdigest())
    grammars.verify(tmp_path, "python")


def test_csharp_library_is_found_under_the_pack_name(tmp_path: Path) -> None:
    path = _fake_library(tmp_path, "c_sharp", b"x")
    assert grammars.library_path(tmp_path, "csharp") == path


def test_unrecorded_platform_fails_for_every_grammar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grammars, "_recorded", dict)
    for grammar in grammars.GRAMMARS:
        _fake_library(tmp_path, grammars._LIBRARY_STEMS.get(grammar, grammar), b"anything")
        with pytest.raises(grammars.GrammarError, match="ハッシュの記録がありません"):
            grammars.verify(tmp_path, grammar)


def test_readme_lists_exactly_the_recorded_platforms() -> None:
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    listed = {
        item.strip("`")
        for item in items(readme, "### 文法の取得")
        if re.fullmatch(r"`[a-z0-9_-]+`", item)
    }
    assert listed == set(grammars._recorded())


def test_missing_grammar_fails_without_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grammars, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        tree_sitter_language_pack,
        "download",
        lambda _names: pytest.fail("実行時に取得しようとした"),
    )
    monkeypatch.setattr(
        tree_sitter_language_pack,
        "get_parser",
        lambda _name: pytest.fail("存在の確認の前に読み込もうとした"),
    )
    with pytest.raises(grammars.GrammarError, match="make setup"):
        grammars.get_parser(grammars.BuiltinGrammar("python"))


def test_unknown_grammar_fails(tmp_path: Path) -> None:
    with pytest.raises(grammars.GrammarError):
        grammars.verify(tmp_path, "ruby")


def test_pinned_grammars_load_from_the_real_store() -> None:
    # 本物の置き場（仮想環境の中）を指す。`make setup` が済んでいなければ、ここで失敗して案内する
    for grammar in grammars.GRAMMARS:
        assert grammars.get_language(grammars.BuiltinGrammar(grammar)) is not None


def test_library_path_ignores_names_with_a_prefix_word(tmp_path: Path) -> None:
    _fake_library(tmp_path, "evil_tree_sitter_python", b"x")
    genuine = _fake_library(tmp_path, "python", b"y")
    assert grammars.library_path(tmp_path, "python") == genuine


def test_ambiguous_library_fails(tmp_path: Path) -> None:
    directory = grammars.library_dir(tmp_path)
    directory.mkdir(parents=True)
    (directory / "libtree_sitter_python.so").write_bytes(b"x")
    (directory / "libtree_sitter_python.dylib").write_bytes(b"y")
    with pytest.raises(grammars.GrammarError, match="複数"):
        grammars.library_path(tmp_path, "python")


def test_pack_reading_a_different_directory_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grammars, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(grammars, "_checked", set())
    monkeypatch.setattr(tree_sitter_language_pack, "cache_dir", lambda: str(tmp_path / "other"))
    path = _fake_library(tmp_path, "python", b"genuine")
    _record(monkeypatch, "python", hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(grammars.GrammarError, match="照合した置き場"):
        grammars.get_parser(grammars.BuiltinGrammar("python"))
