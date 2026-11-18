import json
import tomllib
from pathlib import Path
from typing import Any

from script_loader import import_script

ROOT = Path(__file__).parent.parent
PACK = "tree-sitter-language-pack"
# 例のパッケージ（examples/jigane-language-kotlin）が足す文法。
# 組み込みの記録（grammar-hashes.json）には入らない
EXAMPLE_LANGUAGES = {"kotlin"}


def _allowed_licenses() -> frozenset[str]:
    module = import_script("check_dependency_licenses")
    allowed: frozenset[str] = module.ALLOWED_LICENSES
    return allowed


def _locked_version() -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    (version,) = [package["version"] for package in lock["package"] if package["name"] == PACK]
    return str(version)


def _ledger() -> dict[str, Any]:
    ledger: dict[str, Any] = json.loads(
        (ROOT / "docs" / "grammar-licenses.json").read_text(encoding="utf-8")
    )
    return ledger


def test_ledger_pack_version_matches_the_lock() -> None:
    pack = _ledger()["pack"]
    assert pack["name"] == PACK
    assert pack["version"] == _locked_version()


def test_ledger_licenses_are_allowed() -> None:
    ledger = _ledger()
    entries = [ledger["pack"], *ledger["languages"].values()]
    assert {entry["spdx"] for entry in entries} <= _allowed_licenses()


def test_ledger_languages_match_the_recorded_hashes_and_the_example() -> None:
    hashes = json.loads(
        (ROOT / "jigane" / "languages" / "grammar-hashes.json").read_text(encoding="utf-8")
    )
    recorded = {language for platform in hashes.values() for language in platform}
    assert set(_ledger()["languages"]) == recorded | EXAMPLE_LANGUAGES
