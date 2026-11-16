"""木の中の symlink と junction を、Windows の実ファイルで拒むことの試験。

既定の testpaths の外に置き、Windows の CI の job だけが明示して走らせる。
"""

import _winapi
from pathlib import Path

from jigane.git.tree_files import TreeFileState, read_tree_file, tree_file_state


def _make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.txt").write_text("a", encoding="utf-8")
    return root


def test_symlinked_intermediate_directory_is_refused(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    (root / "in").symlink_to(root / "real", target_is_directory=True)
    assert read_tree_file(root, "in/a.txt") is None
    assert tree_file_state(root, "in/a.txt") is TreeFileState.REFUSED
    assert read_tree_file(root, "real/a.txt") == b"a"


def test_junction_intermediate_directory_is_refused(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    create_junction = vars(_winapi)["CreateJunction"]
    create_junction(str(root / "real"), str(root / "in"))
    assert read_tree_file(root, "in/a.txt") is None
    assert tree_file_state(root, "in/a.txt") is TreeFileState.REFUSED
    assert read_tree_file(root, "real/a.txt") == b"a"
