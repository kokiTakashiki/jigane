"""レビュー対象の木の外へ出るシンボリックリンクを、各読み手が読まないことの確認。"""

import errno
import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
from review_fakes import with_map
from test_bots import (  # noqa: F401 pytest が fixture を名前で引くため import が要る
    comment_for,
    repo,
)

from jigane.git import tree_files
from jigane.git.tree_files import (
    MAX_REVIEW_FILE_BYTES,
    NotOpened,
    TreeFileState,
    TreeFileTooLargeError,
    is_inside_tree,
    read_tree,
    read_tree_bytes,
    read_tree_file,
    read_tree_text,
    tree_file_state,
    walk_tree_files,
)
from jigane.judgment_map.changes import OMITTED_BINARY, OMITTED_TOO_LARGE, file_change
from jigane.judgment_map.classification import FilePair, _Side
from jigane.judgment_map.configuration import Config
from jigane.judgment_map.declaration import extract_declaration, public_names
from jigane.policy_materials import changed_files
from jigane.policy_materials.value_sets import ValueSetLocation
from jigane.pull_request_context import background, bots
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import shards
from jigane.skills.policy_value_sets import _name_referenced

LEAK_MARK = "SECRET_TEXT_FROM_OUTSIDE"


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    directory = tmp_path / "outside"
    directory.mkdir()
    (directory / "secret.md").write_text(f"# Secret\n\n{LEAK_MARK}\n", encoding="utf-8")
    (directory / "Secret.py").write_text(
        f"def {LEAK_MARK.lower()}_name():\n    return 1\n", encoding="utf-8"
    )
    return directory


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    return root


def test_guard_functions(tree: Path, outside: Path) -> None:
    (tree / "real.txt").write_text("ok", encoding="utf-8")
    (tree / "link.txt").symlink_to(outside / "secret.md")
    (tree / "inner.txt").symlink_to(tree / "real.txt")
    (tree / "dirlink").symlink_to(outside)
    assert is_inside_tree(tree, "real.txt")
    assert read_tree_bytes(tree, "real.txt") == b"ok"
    assert read_tree_text(tree, "real.txt") == "ok"
    for escaping in ("link.txt", "inner.txt", "dirlink/secret.md", "../outside/secret.md", "none"):
        assert not is_inside_tree(tree, escaping)
        assert read_tree_bytes(tree, escaping) is None
        assert read_tree_text(tree, escaping) is None


def test_walk_skips_symlinked_files_and_directories(tree: Path, outside: Path) -> None:
    (tree / "sub").mkdir()
    (tree / "sub" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tree / "sub" / "link.py").symlink_to(outside / "Secret.py")
    (tree / "dirlink").symlink_to(outside)
    assert walk_tree_files(tree, tree) == ["sub/a.py"]
    assert walk_tree_files(tree, tree / "sub") == ["sub/a.py"]


def test_declaration_ignores_symlinked_readme_docs_and_targets(tree: Path, outside: Path) -> None:
    (tree / "pkg").mkdir()
    (tree / "pkg" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    (tree / "pkg" / "README.md").symlink_to(outside / "secret.md")
    (tree / "docs").mkdir()
    (tree / "docs" / "design.md").symlink_to(outside / "secret.md")
    (tree / "docs" / "link_dir").symlink_to(outside)
    (tree / "pkg" / "linked.py").symlink_to(outside / "Secret.py")
    declaration = extract_declaration(tree, ["pkg/a.py", "pkg/linked.py"])
    assert all(LEAK_MARK not in e.text for e in declaration.excerpts)
    assert declaration.excerpts == ()
    assert public_names(tree, "pkg/linked.py") == []
    assert public_names(tree, "pkg/a.py") == ["build_widget"]


def test_feature_review_excludes_symlink_to_outside_file(
    tree: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tree / "src").mkdir()
    (tree / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tree / "src" / "leak.py").symlink_to(outside / "Secret.py")
    (tree / "src" / "leak_dir").symlink_to(outside)
    monkeypatch.chdir(tree)
    review = create_feature_review(tree, ["src"], "p")
    assert review.paths == ["src/a.py"]


def test_changes_omits_symlink(tree: Path, outside: Path) -> None:
    (tree / "leak.md").symlink_to(outside / "secret.md")
    lines = file_change(tree, tree, FilePair(None, "leak.md"), Config())
    assert lines[-1] == OMITTED_BINARY
    assert all(LEAK_MARK not in line for line in lines)


def test_judgment_map_side_ignores_symlink(tree: Path, outside: Path) -> None:
    (tree / "leak.py").symlink_to(outside / "Secret.py")
    assert _Side(tree, "leak.py", None).lines == []


def test_shards_skip_symlink(tree: Path, outside: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tree / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tree)
    review = create_feature_review(tree, ["a.py"], "p")
    (tree / "leak.py").symlink_to(outside / "Secret.py")
    units = shards.build_units(replace(review, paths=["a.py", "leak.py"]))
    assert [u.path for u in units] == ["a.py"]


def test_policy_signature_ignores_symlink(
    tree: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tree / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tree)
    (tree / "leak.py").symlink_to(outside / "Secret.py")
    region = {
        "path": "leak.py",
        "qualified_name": f"{LEAK_MARK.lower()}_name",
        "new_span": {"start_line": 1, "end_line": 2},
    }
    review = with_map(
        create_feature_review(tree, ["a.py"], "p"),
        {"regions": [{**region, "id": "J1", "kind": "public_interface"}]},
    )
    assert LEAK_MARK not in repr(changed_files.signature_changes(review))


def test_codeowners_symlink_is_not_read(tree: Path, outside: Path) -> None:
    (outside / "owners").write_text("* @secret-owner\n", encoding="utf-8")
    (tree / "CODEOWNERS").symlink_to(outside / "owners")
    assert background._read_codeowners(tree) == []


def test_blob_reference_ignores_symlink(tree: Path, outside: Path) -> None:
    (tree / "doc.md").symlink_to(outside / "secret.md")
    assert background._existing_file(tree, "doc.md") is None


def test_bot_analysis_ignores_symlinked_head_file(
    repo: dict[str, Any],  # noqa: F811 import した fixture を引数で受けて注入させる
    outside: Path,
) -> None:
    head_file = repo["head"] / "a.py"
    head_file.unlink()
    head_file.symlink_to(outside / "Secret.py")
    analysis = bots.analyze(repo["cache"], repo["head"], [comment_for(repo)])
    assert [j.judged for j in analysis.judgments] == [False]


def test_read_tree_bytes_raises_when_over_max_bytes(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("abcd", encoding="utf-8")
    assert read_tree_bytes(tmp_path, "f.txt", max_bytes=4) == b"abcd"
    with pytest.raises(TreeFileTooLargeError):
        read_tree_bytes(tmp_path, "f.txt", max_bytes=3)
    assert tree_file_state(tmp_path, "f.txt") is TreeFileState.READABLE
    assert tree_file_state(tmp_path, "none.txt") is TreeFileState.MISSING
    assert tree_file_state(tmp_path, "../f.txt") is TreeFileState.REFUSED


def test_changes_omits_file_over_the_size_limit(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("a" * (MAX_REVIEW_FILE_BYTES + 1), encoding="utf-8")
    lines = file_change(tmp_path, tmp_path, FilePair(None, "big.txt"), Config())
    assert lines[-1] == OMITTED_TOO_LARGE
    assert len(lines) == 3


def test_read_tree_file_reads_regular_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"hello")
    assert read_tree_file(tmp_path, "a.txt") == b"hello"


def test_read_tree_file_refuses_symlink_to_outside(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert read_tree_file(root, "link.txt") is None


def test_read_tree_file_refuses_over_limit(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"x" * 11)
    assert read_tree_file(tmp_path, "big.bin", max_bytes=10) is None
    (tmp_path / "ok.bin").write_bytes(b"x" * 10)
    assert read_tree_file(tmp_path, "ok.bin", max_bytes=10) == b"x" * 10


def test_read_tree_file_refuses_escaping_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    assert read_tree_file(root, "../secret.txt") is None
    assert read_tree_file(root, str(tmp_path / "secret.txt")) is None


def test_read_tree_file_refuses_non_regular_file(tmp_path: Path) -> None:
    # FIFO は POSIX にだけある。Windows ではディレクトリで確かめる
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "pipe")
    else:
        (tmp_path / "pipe").mkdir()
    assert read_tree_file(tmp_path, "pipe") is None


def test_read_tree_file_refuses_directory(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    assert read_tree_file(tmp_path, "dir") is None


def test_read_tree_file_refuses_symlinked_intermediate_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.txt").write_text("a", encoding="utf-8")
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "a.txt").write_text("secret", encoding="utf-8")
    (root / "out").symlink_to(outside_directory, target_is_directory=True)
    (root / "in").symlink_to(root / "real", target_is_directory=True)
    assert read_tree_file(root, "out/a.txt") is None
    assert read_tree_file(root, "in/a.txt") is None
    assert tree_file_state(root, "out/a.txt") is TreeFileState.REFUSED
    assert read_tree_file(root, "real/a.txt") == b"a"


def test_read_tree_file_refuses_in_tree_symlink(tmp_path: Path) -> None:
    (tmp_path / "real.txt").write_text("a", encoding="utf-8")
    (tmp_path / "alias.txt").symlink_to(tmp_path / "real.txt")
    assert read_tree_file(tmp_path, "alias.txt") is None
    assert read_tree_bytes(tmp_path, "alias.txt") is None


def test_value_set_reference_skips_symlink_and_oversized_body(tmp_path: Path) -> None:
    definition = ValueSetLocation(path="defs.py", line=1, name="NAMES")
    body = "from defs import NAMES\n"
    (tmp_path / "real.py").write_text(body, encoding="utf-8")
    (tmp_path / "alias.py").symlink_to(tmp_path / "real.py")
    (tmp_path / "big.py").write_text(body + "#" * MAX_REVIEW_FILE_BYTES, encoding="utf-8")
    assert _name_referenced(definition, "real.py", "NAMES", tmp_path)
    assert not _name_referenced(definition, "alias.py", "NAMES", tmp_path)
    assert not _name_referenced(definition, "big.py", "NAMES", tmp_path)


def _raise_os_error(*_args: object, **_kwargs: object) -> NoReturn:
    raise OSError("simulated")


def test_read_tree_file_returns_none_when_fstat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(os, "fstat", _raise_os_error)
    assert read_tree_file(tmp_path, "a.txt") is None
    assert read_tree_bytes(tmp_path, "a.txt") is None
    assert tree_file_state(tmp_path, "a.txt") is TreeFileState.UNREADABLE


def test_read_tree_file_returns_none_when_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(os, "fdopen", _raise_os_error)
    assert read_tree_file(tmp_path, "a.txt") is None
    assert read_tree_bytes(tmp_path, "a.txt", max_bytes=10) is None


def test_read_tree_file_follows_symlinked_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.txt").write_text("a", encoding="utf-8")
    link = tmp_path / "root-link"
    link.symlink_to(real, target_is_directory=True)
    assert read_tree_file(link, "a.txt") == b"a"
    assert tree_file_state(link, "a.txt") is TreeFileState.READABLE


@pytest.mark.parametrize("walk", [True, False])
def test_tree_file_states_in_both_open_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, walk: bool
) -> None:
    monkeypatch.setattr(
        tree_files, "_CAN_WALK_BY_DIRECTORY", walk and tree_files._CAN_WALK_BY_DIRECTORY
    )
    root = tmp_path / "root"
    (root / "d").mkdir(parents=True)
    (root / "d" / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("s", encoding="utf-8")
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")
    (root / "dlink").symlink_to(root / "d", target_is_directory=True)
    assert tree_file_state(root, "d/a.txt") is TreeFileState.READABLE
    assert tree_file_state(root, "none.txt") is TreeFileState.MISSING
    assert tree_file_state(root, "d/a.txt/b") is TreeFileState.MISSING
    assert tree_file_state(root, "d") is TreeFileState.NOT_REGULAR
    assert tree_file_state(root, "link.txt") is TreeFileState.REFUSED
    assert tree_file_state(root, "../secret.txt") is TreeFileState.REFUSED
    assert read_tree_file(root, "d/a.txt") == b"a"
    assert read_tree_file(root, "link.txt") is None
    # 途中の要素のリンクは、どちらの経路でも拒む
    assert read_tree_file(root, "dlink/a.txt") is None


def test_unreadable_is_distinct_from_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise PermissionError(errno.EACCES, "denied")

    real_open = os.open
    monkeypatch.setattr(os, "open", refuse)
    assert tree_file_state(tmp_path, "a.txt") is TreeFileState.UNREADABLE
    assert read_tree_bytes(tmp_path, "a.txt") is None
    monkeypatch.setattr(os, "open", real_open)
    assert tree_file_state(tmp_path, "none") is TreeFileState.MISSING


def test_lstat_path_refuses_file_swapped_for_another_after_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows 相当の経路で、lstat と open の間に別のファイルへ差し替えられたら拒む。"""
    monkeypatch.setattr(tree_files, "_CAN_WALK_BY_DIRECTORY", False)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "other.txt").write_text("secret", encoding="utf-8")
    real_open = os.open
    swapped: list[bool] = []

    def swap_then_open(path: Path, flags: int) -> int:
        if Path(path).name == "a.txt" and not swapped:
            swapped.append(True)
            (tmp_path / "other.txt").replace(tmp_path / "a.txt")
        return real_open(path, flags)

    monkeypatch.setattr(os, "open", swap_then_open)
    assert read_tree(tmp_path, "a.txt", None) == NotOpened(TreeFileState.REFUSED)
    assert swapped == [True]


def test_lstat_path_reads_unswapped_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tree_files, "_CAN_WALK_BY_DIRECTORY", False)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.txt").write_text("a", encoding="utf-8")
    assert read_tree_file(tmp_path, "d/a.txt") == b"a"
    assert tree_file_state(tmp_path, "d/none") is TreeFileState.MISSING


def test_lstat_path_refuses_path_that_resolves_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tree_files, "_CAN_WALK_BY_DIRECTORY", False)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    real_resolve = Path.resolve

    def resolve(self: Path, *, strict: bool = False) -> Path:
        if self.name == "a.txt":
            return Path("/outside-the-root/a.txt")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert read_tree(tmp_path, "a.txt", None) == NotOpened(TreeFileState.REFUSED)


def test_lstat_path_refuses_reparse_point_intermediate_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows の junction は S_ISLNK にならない。再解析ポイントの属性だけで中間の要素を拒む。"""
    monkeypatch.setattr(tree_files, "_CAN_WALK_BY_DIRECTORY", False)
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.txt").write_text("a", encoding="utf-8")
    real_lstat = os.lstat

    def lstat(path: Path) -> os.stat_result:
        status = real_lstat(path)
        if Path(path).name != "in":
            return status
        return cast(
            "os.stat_result",
            SimpleNamespace(
                st_mode=status.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
            ),
        )

    monkeypatch.setattr(os, "lstat", lstat)
    assert read_tree(tmp_path, "in/a.txt", None) == NotOpened(TreeFileState.REFUSED)


@pytest.mark.parametrize("walk", [True, False])
def test_directory_is_not_regular_when_open_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, walk: bool
) -> None:
    """Windows はディレクトリの open を EACCES で拒む。それでも NOT_REGULAR になる。"""
    monkeypatch.setattr(
        tree_files, "_CAN_WALK_BY_DIRECTORY", walk and tree_files._CAN_WALK_BY_DIRECTORY
    )
    (tmp_path / "d").mkdir()
    real_open = os.open

    def deny_directory(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(str(path)).name == "d":
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", deny_directory)
    assert tree_file_state(tmp_path, "d") is TreeFileState.NOT_REGULAR
