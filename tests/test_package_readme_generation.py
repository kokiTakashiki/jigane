import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent


def load_generator() -> ModuleType:
    path = ROOT / "scripts" / "generate_package_readme.py"
    spec = importlib.util.spec_from_file_location("generate_package_readme", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_exact(path: Path) -> str:
    # read_text は newline を受け取れないので、改行を変えずに読む
    return path.read_bytes().decode("utf-8")


def copy_tree(destination: Path) -> Path:
    """生成器が読むファイルだけを一時の場所へ写す。"""
    shutil.copy(ROOT / "pyproject.toml", destination / "pyproject.toml")
    for readme in (ROOT / "jigane").glob("*/README.md"):
        target = destination / "jigane" / readme.parent.name / "README.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(readme, target)
    shutil.copy(ROOT / "jigane" / "README.md", destination / "jigane" / "README.md")
    return destination


def test_package_readme_matches_the_generated_one() -> None:
    generator = load_generator()
    current = read_exact(ROOT / "jigane" / "README.md")
    assert current == generator.generate(ROOT)


def test_check_flag_reports_a_stale_table(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    readme = root / "jigane" / "README.md"
    stale = readme.read_text(encoding="utf-8").replace("| [`git`]", "| [`gitt`]", 1)
    readme.write_text(stale, encoding="utf-8")
    assert generator.main(["--root", str(root), "--check"]) == 1
    assert generator.main(["--root", str(root)]) == 0
    assert generator.main(["--root", str(root), "--check"]) == 0


def test_added_subpackage_appears_in_table_and_diagram(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(
        text.replace('    "commands",\n', '    "commands : newcomer",\n', 1), encoding="utf-8"
    )
    newcomer = root / "jigane" / "newcomer"
    newcomer.mkdir()
    (newcomer / "README.md").write_text(
        "# newcomer\n\n新しい部品を受け持ちます。詳細は略。\n", encoding="utf-8"
    )
    generated = generator.generate(root)
    assert "| [`newcomer`](newcomer/README.md) | 新しい部品を受け持ちます。 |" in generated
    assert "    subgraph cli\n        commands\n        newcomer\n    end" in generated


def test_crlf_readme_keeps_everything_outside_the_markers(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    readme = root / "jigane" / "README.md"
    original = read_exact(readme).replace("\n", "\r\n")
    with readme.open("w", encoding="utf-8", newline="") as destination:
        destination.write(original)
    assert generator.main(["--root", str(root)]) == 0
    regenerated = readme.read_bytes().decode("utf-8")
    assert regenerated == original
    assert "\n" not in regenerated.replace("\r\n", "")
    outside = "# jigane\r\n\r\n"
    assert regenerated.startswith(outside)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write_readme(root: Path, name: str, text: str) -> None:
    (root / "jigane" / name / "README.md").write_text(text, encoding="utf-8")


def test_a_duplicated_subpackage_in_layers_is_rejected(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    edit(root / "pyproject.toml", '    "commands",\n', '    "commands : git",\n')
    with pytest.raises(ValueError, match="重複"):
        generator.generate(root)


def test_a_subpackage_without_readme_is_rejected(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    (root / "jigane" / "git" / "README.md").unlink()
    with pytest.raises(ValueError, match="の無いサブパッケージ"):
        generator.generate(root)


def test_a_missing_marker_pair_is_rejected(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    edit(root / "jigane" / "README.md", "<!-- END GENERATED: package-table -->", "")
    with pytest.raises(ValueError, match="印"):
        generator.generate(root)


def test_a_first_paragraph_without_a_period_is_rejected(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    write_readme(root, "git", "# git\n\n句点が無い\n")
    with pytest.raises(ValueError, match="句点"):
        generator.generate(root)


def test_a_pipe_in_a_role_is_escaped_in_the_table(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    write_readme(root, "git", "# git\n\na|b を持ちます。\n")
    assert "| [`git`](git/README.md) | a\\|b を持ちます。 |" in generator.generate(root)


def test_main_reports_a_rejected_input_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    generator = load_generator()
    root = copy_tree(tmp_path)
    (root / "jigane" / "git" / "README.md").unlink()
    assert generator.main(["--root", str(root), "--check"]) == 1
    assert capsys.readouterr().err.count("\n") == 1
