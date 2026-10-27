from pathlib import Path
from unittest import mock

from jigane.git.tree_files import read_tree_text
from jigane.skills import policy_tree


def _write(root: Path, name: str, text: str | bytes) -> None:
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        file.write_bytes(text)
    else:
        file.write_text(text, encoding="utf-8")


def test_skips_large_binary_and_symlink_but_lists_large_and_binary_paths(tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "big.py", "x" * (policy_tree.MAX_FILE_BYTES + 1))
    _write(tmp_path, "bin.dat", b"a\x00b")
    (tmp_path / "link.py").symlink_to(tmp_path / "ok.py")
    tree = policy_tree.read_tree(tmp_path)
    assert set(tree.files) == {"ok.py"}
    assert {"ok.py", "big.py", "bin.dat"} <= set(tree.paths)
    assert "link.py" not in tree.paths


def test_marks_tests_and_language(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", "x = 1\n")
    _write(tmp_path, "tests/test_a.py", "x = 1\n")
    _write(tmp_path, "notes.txt", "hello\n")
    tree = policy_tree.read_tree(tmp_path)
    assert tree.files["src/a.py"].is_test is False
    assert tree.files["tests/test_a.py"].is_test is True
    assert tree.files["notes.txt"].adapter is None
    assert tree.files["src/a.py"].adapter is not None
    assert {f.path for f in tree.source_files()} == {"src/a.py", "tests/test_a.py"}


def test_reads_each_file_once(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    _write(tmp_path, "b.py", "y = 2\n")
    with mock.patch.object(policy_tree, "read_tree_text", wraps=read_tree_text) as reader:
        policy_tree.read_tree(tmp_path)
    assert sorted(call.args[1] for call in reader.call_args_list) == ["a.py", "b.py"]
