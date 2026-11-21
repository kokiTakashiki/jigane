"""POSIX だけで意味を持つ試験の置き場。既定の testpaths の外に置く。

macOS と Linux のゲート（Makefile の test）だけが走らせる。
Windows のゲート（justfile の test）は走らせない。
置き場の違いで走る OS が決まるため、条件付きの skip は使わない。tests_windows と対になる。
"""

from pathlib import Path

import pytest
from pr_support import commit_all, git
from test_run_record import review  # noqa: F401 pytest が fixture を名前で引くため import が要る
from test_security import project  # noqa: F401 pytest が fixture を名前で引くため import が要る

from jigane.commands import main
from tests_posix.codex_cli_support import (
    codex_trace,  # noqa: F401 pytest が fixture を名前で引くため import が要る
)

pytest_plugins = ["shared_fixtures", "language_plugin_support"]


@pytest.fixture
def synthetic_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "remote", "add", "origin", "https://github.com/octo/synthetic.git")
    source = repo / "sample.py"
    source.write_text("def sample():\n    return 1\n", encoding="utf-8")
    commit_all(repo, "initial")
    source.write_text("def sample():\n    return 2\n", encoding="utf-8")
    commit_all(repo, "change the return value")
    monkeypatch.chdir(repo)
    assert main(["new", "range", "HEAD~1..HEAD", "--name", "sample"]) == 0
    return tmp_path
