"""レビュー対象の木の外へ出るシンボリックリンクを、各読み手が読まないことの確認。"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from review_fakes import with_map
from test_bots import comment_for, repo  # noqa: F401 pytest が fixture を名前で引くため import が要る

from jigane.git.tree_files import (
    MAX_REVIEW_FILE_BYTES,
    TreeFileTooLargeError,
    is_inside_tree,
    read_tree_bytes,
    read_tree_text,
    tree_file_state,
    walk_tree_files,
)
from jigane.judgment_map.changes import OMITTED_BINARY, OMITTED_TOO_LARGE, file_change
from jigane.judgment_map.classification import FilePair, _Side
from jigane.judgment_map.configuration import Config
from jigane.judgment_map.declaration import extract_declaration, public_names
from jigane.policy_materials import changed_files
from jigane.pull_request_context import background, bots
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import shards

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
    assert tree_file_state(tmp_path, "f.txt") == "readable"
    assert tree_file_state(tmp_path, "none.txt") == "missing"
    assert tree_file_state(tmp_path, "../f.txt") == "refused"


def test_changes_omits_file_over_the_size_limit(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("a" * (MAX_REVIEW_FILE_BYTES + 1), encoding="utf-8")
    lines = file_change(tmp_path, tmp_path, FilePair(None, "big.txt"), Config())
    assert lines[-1] == OMITTED_TOO_LARGE
    assert len(lines) == 3
