from pathlib import Path

import pytest

from jigane.git import process
from jigane.git.repository import Repository
from jigane.repository_docs.classify import Inside, Outside, classify_path


def make_repository(root: Path, origin: str | None) -> None:
    root.mkdir(parents=True)
    process.run_command(["git", "init", "-q"], cwd=root)
    if origin is not None:
        process.run_command(["git", "remote", "add", "origin", origin], cwd=root)


def test_path_in_checkout_of_the_same_repository_is_inside(tmp_path: Path) -> None:
    make_repository(tmp_path / "work", "https://github.com/Example-Org/Example-Repo.git")
    (tmp_path / "work" / "docs").mkdir()

    result = classify_path(str(tmp_path / "work" / "docs" / "a.md"), "example-org", "example-repo")

    assert result == Inside((tmp_path / "work").resolve(), "docs/a.md")


def test_other_repository_and_non_git_paths_are_outside(tmp_path: Path) -> None:
    make_repository(tmp_path / "work", "https://github.com/other/repo.git")
    plain = tmp_path / "plain"
    plain.mkdir()

    assert isinstance(classify_path(str(tmp_path / "work" / "a.md"), "a", "b"), Outside)
    assert isinstance(classify_path(str(plain / "a.md"), "a", "b"), Outside)
    assert isinstance(classify_path(str(tmp_path / "no" / "such" / "a.md"), "a", "b"), Outside)


def test_repository_without_origin_has_owner_local(tmp_path: Path) -> None:
    make_repository(tmp_path / "work", None)

    assert isinstance(classify_path(str(tmp_path / "work" / "a.md"), "local", "work"), Inside)
    assert isinstance(classify_path(str(tmp_path / "work" / "a.md"), "a", "work"), Outside)


def test_work_side_link_is_followed_before_classifying(tmp_path: Path) -> None:
    make_repository(tmp_path / "work", "https://github.com/a/b.git")
    (tmp_path / "work" / "AGENTS.md").write_text("x", encoding="utf-8")
    link = tmp_path / "work" / "CLAUDE.md"
    link.symlink_to("AGENTS.md")

    assert classify_path(str(link), "a", "b") == Inside((tmp_path / "work").resolve(), "AGENTS.md")


def test_path_outside_the_reported_root_is_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # identify_git が根を返せても、解いたパスがその根の下に無ければ中として扱わない
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setattr(
        "jigane.repository_docs.classify.identify_git",
        lambda _directory: Repository(tmp_path / "elsewhere", "a", "b"),
    )

    assert isinstance(classify_path(str(tmp_path / "a.md"), "a", "b"), Outside)
