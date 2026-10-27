"""作業ディレクトリの走査が、`.claude/worktrees/<名前>/` の他の作業の写しを読まないことの確認。"""

import ast
from pathlib import Path

import pytest

from jigane.git.process import run_command
from jigane.git.tree_files import work_files

PACKAGE = Path(__file__).resolve().parent.parent / "jigane"
# 走査の部品。リポジトリの走査はここを通す
COMPONENT = PACKAGE / "git" / "tree_files.py"


def _write(path: Path, text: str = "x = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_into(copy: Path, *, with_repository_marker: bool) -> None:
    _write(copy / "copy.py")
    _write(copy / "sub" / "copy_deep.py")
    if with_repository_marker:
        (copy / ".git").mkdir()


@pytest.fixture
def work_tree(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    run_command(["git", "init", "-q"], cwd=root)
    _write(root / "tracked.py")
    run_command(["git", "add", "tracked.py"], cwd=root)
    _write(root / "untracked.py")
    _copy_into(root / ".claude" / "worktrees" / "x", with_repository_marker=True)
    return root


def test_work_tree_excludes_nested_copy(work_tree: Path) -> None:
    assert work_files(work_tree).files == ("tracked.py",)
    found = work_files(work_tree, include_untracked=True)
    assert found.files == ("tracked.py", "untracked.py")
    assert found.untracked == {"untracked.py"}


def test_work_tree_excludes_hidden_copy_without_its_own_marker(work_tree: Path) -> None:
    # 写しに .git が無くても、隠しディレクトリの下の追跡外は読まない
    (work_tree / ".claude" / "worktrees" / "x" / ".git").rmdir()
    assert work_files(work_tree, include_untracked=True).files == ("tracked.py", "untracked.py")


def test_plain_directory_excludes_hidden_and_nested_working_trees(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    _write(root / "a.py")
    _write(root / "sub" / "b.py")
    _copy_into(root / ".claude" / "worktrees" / "x", with_repository_marker=True)
    _copy_into(root / "vendored_copy", with_repository_marker=True)
    assert work_files(root).files == ("a.py", "sub/b.py")
    assert work_files(root, include_untracked=True).untracked == frozenset()


def _raw_walk_lines(source: str) -> list[int]:
    """`<式>.rglob(...)` と `os.walk(...)` の呼び出しの行番号。"""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            node.func.attr == "rglob"
            or (
                node.func.attr == "walk"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            )
        )
    ]


def test_scans_go_through_the_component() -> None:
    offenders = [
        f"{path.relative_to(PACKAGE.parent)}:{number}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if path != COMPONENT
        for number in _raw_walk_lines(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_a_direct_scan_is_found_however_it_is_written() -> None:
    assert _raw_walk_lines("root.rglob('*.py')\n") == [1]
    assert _raw_walk_lines("import os\nlist(os.walk(top))\n") == [2]
    assert _raw_walk_lines("(\n    root\n    .rglob('*')\n)\n") == [2]
    assert _raw_walk_lines("walk(top)\nx = 'rglob('\n# os.walk(top)\n") == []


def test_missing_root_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        work_files(tmp_path / "missing")
