from pathlib import Path

import pytest
from pr_support import commit_all, git
from public_sync_support import build_world, run_sync, write_files


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    return build_world(tmp_path)


def test_the_sync_copies_an_executable_bit_and_replaces_symlinks(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    public = world["public"]
    tree = world["tree"]
    write_files(public, {"run.sh": "echo\n", "swap.txt": "regular\n"})
    (public / "link").symlink_to("keep.txt")
    commit_all(public, "script and links")
    git(public, "push", "origin", "main")
    write_files(tree, {"run.sh": "echo\n"})
    (tree / "run.sh").chmod(0o755)
    (tree / "link").symlink_to("changed.txt")
    (tree / "swap.txt").symlink_to("keep.txt")

    _, out, _ = run_sync(world, capsys=capsys)

    assert "新規: 1 件、更新: 5 件" in out
    assert (public / "run.sh").stat().st_mode & 0o111
    assert (public / "link").readlink() == Path("changed.txt")
    assert (public / "swap.txt").readlink() == Path("keep.txt")


def test_an_unchanged_symlink_is_not_counted_as_updated(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    (world["public"] / "link").symlink_to("keep.txt")
    commit_all(world["public"], "link")
    git(world["public"], "push", "origin", "main")
    (world["tree"] / "link").symlink_to("keep.txt")

    _, out, _ = run_sync(world, capsys=capsys)

    assert "新規: 1 件、更新: 2 件" in out
