import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import grimp
import pytest
from dev_launch import run_tool

from jigane.git.process import CommandError

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "generate_readme_dependencies.py"


def load_generator() -> ModuleType:
    path = ROOT / "scripts" / "generate_readme_dependencies.py"
    spec = importlib.util.spec_from_file_location("generate_readme_dependencies", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_readmes(destination: Path) -> Path:
    """生成器が読み書きする README と、サブパッケージを見つける __init__.py だけを写す。"""
    for readme in (ROOT / "jigane").glob("*/README.md"):
        target = destination / "jigane" / readme.parent.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy(readme, target / "README.md")
        (target / "__init__.py").write_text("", encoding="utf-8")
    return destination


def test_every_readme_matches_the_generated_dependencies() -> None:
    generator = load_generator()
    expected = generator.generate(ROOT, generator.measure(ROOT))
    current = {
        name: (ROOT / "jigane" / name / "README.md").read_bytes().decode("utf-8")
        for name in expected
    }
    assert current == expected


def test_readme_without_the_marker_is_rejected(tmp_path: Path) -> None:
    generator = load_generator()
    root = copy_readmes(tmp_path)
    readme = root / "jigane" / "groups" / "README.md"
    text = readme.read_text(encoding="utf-8")
    readme.write_text(text.replace(generator.BEGIN, ""), encoding="utf-8")
    with pytest.raises(ValueError, match=r"jigane/groups/README\.md"):
        generator.generate(root, set())


def write_small_tree(root: Path, begin: str, end: str) -> None:
    """storage が groups を読むだけの小さな木。README の一覧は空のまま（古い）にしておく。"""
    for name, source in (("groups", ""), ("storage", "from jigane import groups\n")):
        package = root / "jigane" / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(source, encoding="utf-8")
        (package / "README.md").write_text(
            f"# {name}\n\n## 他のモジュールとの関係\n\n{begin}\n{end}\n", encoding="utf-8"
        )
    (root / "jigane" / "__init__.py").write_text("", encoding="utf-8")


def run_generator(root: Path, *flags: str) -> None:
    # 台本は子プロセスで走らせる。測りが sys.modules と sys.path を変えるため
    run_tool([sys.executable, str(SCRIPT), "--root", str(root), *flags])


def test_check_flag_measures_the_given_tree_and_reports_a_stale_list(tmp_path: Path) -> None:
    generator = load_generator()
    write_small_tree(tmp_path, generator.BEGIN, generator.END)
    with pytest.raises(CommandError) as stale:
        run_generator(tmp_path, "--check")
    assert stale.value.returncode == 1
    run_generator(tmp_path)
    run_generator(tmp_path, "--check")
    storage = (tmp_path / "jigane" / "storage" / "README.md").read_text(encoding="utf-8")
    assert "読むサブパッケージ：`groups`" in storage
    groups = (tmp_path / "jigane" / "groups" / "README.md").read_text(encoding="utf-8")
    assert "読まれるサブパッケージ：`storage`" in groups


def build_graph(edges: dict[str, list[str]]) -> grimp.ImportGraph:
    graph = grimp.ImportGraph()
    for importer, imported_modules in edges.items():
        graph.add_module(importer)
        for imported in imported_modules:
            graph.add_import(importer=importer, imported=imported)
    return graph


def test_collect_edges_folds_child_packages_into_the_parent() -> None:
    generator = load_generator()
    graph = build_graph(
        {
            "jigane.commands.subcommands.run": ["jigane.skills.macro", "jigane.commands.failures"],
            "jigane.skills.macro": ["jigane.storage.store"],
            "jigane.__main__": ["jigane.commands.main"],
            "jigane.storage.store": ["jigane.__main__"],
        }
    )
    edges = generator.collect_edges(graph, ["commands", "storage", "skills"])
    assert edges == {("commands", "skills"), ("skills", "storage")}


def test_render_region_sorts_names_and_writes_none_for_empty() -> None:
    generator = load_generator()
    edges = {("b", "a"), ("c", "a"), ("a", "z"), ("a", "m")}
    region = generator.render_region("a", edges, "\n")
    assert "読むサブパッケージ：`m`、`z`\n" in region
    assert "読まれるサブパッケージ：`b`、`c`\n" in region
    empty = generator.render_region("z", set(), "\n")
    assert "読むサブパッケージ：なし\n" in empty
    assert "読まれるサブパッケージ：なし\n" in empty
