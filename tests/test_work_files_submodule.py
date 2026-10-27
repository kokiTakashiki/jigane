"""サブモジュールの中の追跡ファイルが、親の根からの綴りで作業ファイルに入ることの確認。"""

from pathlib import Path

import pytest
from pr_support import git

from jigane.design.parse import parse_design
from jigane.design.recommend import recommend
from jigane.git.tree_files import work_files
from jigane.policy_materials.current_shape import current_shape
from jigane.reviews.lifecycle import judge_files

CART = """\
class Cart:
    def checkout(self) -> int:
        return 1
"""
DOCUMENT = "# 題\n\n## 1. 序論\n\n`Cart` を直す。\n"
SUBMODULE_FILE = "cli/jigane/cart.py"


def _commit_file(repo: Path, relative: str, text: str) -> None:
    (repo / relative).parent.mkdir(parents=True, exist_ok=True)
    (repo / relative).write_text(text, encoding="utf-8")
    git(repo, "add", relative)
    git(repo, "commit", "-q", "-m", "add " + relative)


@pytest.fixture
def parent(tmp_path: Path) -> Path:
    child = tmp_path / "child"
    child.mkdir()
    git(child, "init", "-q")
    _commit_file(child, "jigane/cart.py", CART)
    root = tmp_path / "parent"
    root.mkdir()
    git(root, "init", "-q")
    _commit_file(root, "README.md", "# parent\n")
    # ネットワークを使わない。ローカルの子を file プロトコルで取り込む
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(child), "cli")
    git(root, "commit", "-q", "-m", "add submodule")
    return root


def test_files_inside_a_submodule_are_listed_from_the_parent_root(parent: Path) -> None:
    found = work_files(parent, include_untracked=True)
    assert SUBMODULE_FILE in found.files
    # gitlink のディレクトリ名そのものはファイルではない
    assert "cli" not in found.files
    assert SUBMODULE_FILE not in found.untracked


def test_untracked_files_inside_a_submodule_are_not_collected(parent: Path) -> None:
    (parent / "cli" / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    (parent / "top.py").write_text("x = 1\n", encoding="utf-8")
    found = work_files(parent, include_untracked=True)
    assert "cli/scratch.py" not in found.files
    assert "top.py" in found.untracked


def test_uninitialized_submodule_does_not_fail(parent: Path, tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--no-local", str(parent), str(clone))
    found = work_files(clone, include_untracked=True)
    assert sorted(found.files) == [".gitmodules", "README.md"]


def test_recommend_finds_a_declaration_inside_a_submodule(parent: Path) -> None:
    result = recommend(parent, parse_design(DOCUMENT))
    assert SUBMODULE_FILE in {c.path for c in result.candidates}


def test_current_shape_draws_a_type_inside_a_submodule(parent: Path) -> None:
    shape = current_shape(parent, work_files(parent).files, ["cli.jigane.cart.Cart"])
    assert "class Cart[" in shape.classes.panels[0].mermaid


def test_judgment_map_covers_a_submodule_file(parent: Path) -> None:
    judgment_map = judge_files(parent, [SUBMODULE_FILE])
    assert {segment.new_path for segment in judgment_map.segments} == {SUBMODULE_FILE}
