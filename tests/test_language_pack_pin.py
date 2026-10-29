import tomllib
from pathlib import Path

from jigane.languages.grammars import pack_version

ROOT = Path(__file__).parent.parent
PACK = "tree-sitter-language-pack"


def _pinned_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies: list[str] = project["dependencies"]
    (requirement,) = [line for line in dependencies if line.startswith(PACK)]
    name, separator, version = requirement.partition("==")
    # 範囲や複数の条件ではなく、一つの版への固定であること
    assert (name, separator) == (PACK, "==")
    assert version.replace(".", "").isdigit()
    return version


def _locked_version() -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    (version,) = [package["version"] for package in lock["package"] if package["name"] == PACK]
    return str(version)


def test_pack_is_pinned_to_the_locked_and_installed_version() -> None:
    # 組み込みの文法の記録は、入っているパックの版（pack_version）で取る。
    # 配布物の利用者に別の版が入ると、記録と合わずに文法を読み込めない
    pinned = _pinned_version()
    assert pinned == _locked_version()
    assert pinned == pack_version()
