import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent


def load_generator() -> ModuleType:
    path = ROOT / "scripts" / "generate_import_contracts.py"
    spec = importlib.util.spec_from_file_location("generate_import_contracts", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pyproject_text() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def forbidden_of(contracts: str, name: str) -> str:
    """生成した契約の全文から、サブパッケージ一つの forbidden_modules の中身を取り出す。"""
    return contracts.split(f'source_modules = ["jigane.{name}"]')[1].split("]", maxsplit=1)[0]


def test_pyproject_contracts_match_the_generated_ones() -> None:
    generator = load_generator()
    text = pyproject_text()
    assert text == generator.generate(text)


def test_check_flag_reports_a_stale_region(tmp_path: Path) -> None:
    generator = load_generator()
    stale = pyproject_text().replace('"jigane.regex",', '"jigane.regex!",', 1)
    assert stale != pyproject_text()
    (tmp_path / "pyproject.toml").write_text(stale, encoding="utf-8")
    assert generator.main(["--root", str(tmp_path), "--check"]) == 1
    assert generator.main(["--root", str(tmp_path)]) == 0
    assert generator.main(["--root", str(tmp_path), "--check"]) == 0


def test_an_undeclared_sibling_dependency_is_forbidden() -> None:
    generator = load_generator()
    contracts = generator.render_contracts(["top", "a : b : c", "x"], {"a": ["b"]})
    forbidden_a = forbidden_of(contracts, "a")
    assert '"jigane.c"' in forbidden_a
    assert '"jigane.b"' not in forbidden_a
    forbidden_b = forbidden_of(contracts, "b")
    assert '"jigane.a"' in forbidden_b
    assert '"jigane.c"' in forbidden_b


def test_declaring_a_dependency_removes_it_from_the_forbidden_list() -> None:
    generator = load_generator()
    layers = ["a : b"]
    assert '"jigane.b"' in forbidden_of(generator.render_contracts(layers, {}), "a")
    assert '"jigane.b"' not in forbidden_of(generator.render_contracts(layers, {"a": ["b"]}), "a")


@pytest.mark.parametrize(
    ("allowed", "message"),
    [
        ({"a": ["z"]}, "同じ層の兄弟でない"),
        ({"a": ["a"]}, "同じ層の兄弟でない"),
        ({"a": ["b", "b"]}, "重複"),
        ({"top": ["a"]}, "兄弟を持たない"),
        ({"q": ["a"]}, "層にない"),
    ],
)
def test_a_table_that_is_not_among_same_layer_siblings_is_rejected(
    allowed: dict[str, list[str]], message: str
) -> None:
    generator = load_generator()
    with pytest.raises(ValueError, match=message):
        generator.render_contracts(["top", "a : b", "x"], allowed)


def test_a_missing_marker_pair_is_rejected() -> None:
    generator = load_generator()
    with pytest.raises(ValueError, match="印"):
        generator.replace_region("[tool]\n", "x")


def test_a_crlf_pyproject_keeps_its_line_endings_outside_the_region(tmp_path: Path) -> None:
    generator = load_generator()
    crlf = pyproject_text().replace("\n", "\r\n")
    stale = crlf.replace('"jigane.regex",', '"jigane.regex!",', 1)
    assert stale != crlf
    target = tmp_path / "pyproject.toml"
    target.write_bytes(stale.encode("utf-8"))
    assert generator.main(["--root", str(tmp_path)]) == 0
    assert target.read_bytes() == crlf.encode("utf-8")
    assert generator.main(["--root", str(tmp_path), "--check"]) == 0
