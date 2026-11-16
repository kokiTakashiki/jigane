"""POSIX のファイル名がバイト列であることに頼る、PR の木の取り出しの試験。"""

import os
from pathlib import Path

import pytest
from pr_support import git
from test_pull_request import _blob, _raw_tree_commit

from jigane.pull_request_context.fetching import _read_tree


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    return path


def test_read_tree_keeps_non_utf8_path(repo: Path) -> None:
    blob = _blob(repo, "seed", "x")
    commit = _raw_tree_commit(repo, [("100644", os.fsdecode(b"\xff.txt"), blob)])
    entries = _read_tree(repo, commit)
    assert [entry.path for entry in entries] == [os.fsdecode(b"\xff.txt")]
