import sys
from pathlib import Path

import pytest
from dev_launch import run_tool

from jigane.git.process import CommandError, run_command


def test_run_tool_strips_git_location_variables_from_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_command(["git", "init", "-q", str(tmp_path)])
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    # GIT_DIR が子に渡れば存在しない場所を指して失敗する。除かれていれば cwd のリポジトリを使う
    assert run_tool(["git", "rev-parse", "--git-dir"], cwd=tmp_path).strip() == ".git"


def test_run_tool_keeps_the_environment_of_other_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", "kept")
    code = "import os; print(os.environ['GIT_DIR'])"
    assert run_tool([sys.executable, "-c", code]).strip() == "kept"


def test_run_tool_turns_a_missing_executable_into_command_error() -> None:
    with pytest.raises(CommandError) as raised:
        run_tool(["jigane-no-such-tool-for-test"])
    assert raised.value.returncode is None
    assert raised.value.missing_executable is True
