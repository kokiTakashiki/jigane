"""git、gh、claude が無いときとログインしていないときの、失敗の種類と次の操作。"""

from pathlib import Path

import pytest

from jigane.commands import failures
from jigane.git import process
from jigane.git.process import CommandError
from jigane.llm_runs.claude import (
    ClaudeInvocationError,
    Invocation,
    LaunchBudget,
    parse_record,
    run,
)
from jigane.pull_request_context import fetching as pull_request
from jigane.reviews.lifecycle import ReviewError, resolve_range

MISSING_TITLE = "必要なコマンドが見つかりません"


@pytest.fixture
def empty_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def assert_missing(error: CommandError, executable: str, action_part: str) -> None:
    assert error.missing_executable
    failure = failures.describe(error)
    assert failure.kind == "missing_tool"
    assert failure.title == MISSING_TITLE
    assert f"実行ファイルが見つかりません: {executable}" in failure.reason
    assert action_part in failure.action


@pytest.mark.parametrize(
    ("executable", "action_part"),
    [("git", "git をインストールして PATH に通してください"), ("gh", "`gh auth login`")],
)
@pytest.mark.usefixtures("empty_path")
def test_missing_git_or_gh_names_the_tool_and_next_step(
    executable: str, action_part: str, tmp_path: Path
) -> None:
    with pytest.raises(CommandError) as raised:
        process.run_command([executable, "x"], cwd=tmp_path)
    assert_missing(raised.value, executable, action_part)


@pytest.mark.usefixtures("empty_path")
def test_missing_claude_names_the_tool_and_login_step(tmp_path: Path) -> None:
    invocation = Invocation(
        prompt="review",
        json_schema={"type": "object"},
        working_directory=tmp_path,
        budget=LaunchBudget(limit_usd=0.6, cli_usd=0.5),
        model="claude-sonnet-5-5",
    )
    with pytest.raises(CommandError) as raised:
        run(invocation)
    assert_missing(raised.value, "claude", "ログインしてください")


def test_logged_out_claude_points_at_the_login_check() -> None:
    # 本物のログイン切れの出力の形は既存のコードに無い。ここでは失敗した起動の一般の形で確かめる
    error = ClaudeInvocationError(
        "claude の終了コードが 1 でした: Not logged in", parse_record({}, 0.5)
    )
    failure = failures.describe(error)
    assert failure.kind == "claude"
    assert "ログインしているか" in failure.action


@pytest.mark.usefixtures("empty_path")
def test_range_without_git_reports_the_missing_tool_not_a_bad_ref(tmp_path: Path) -> None:
    with pytest.raises(CommandError) as raised:
        resolve_range(tmp_path, "a..b")
    assert_missing(raised.value, "git", "git をインストール")
    assert not isinstance(raised.value, ReviewError)


@pytest.mark.usefixtures("empty_path")
def test_pull_request_without_git_stops_before_touching_the_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "home"))
    with pytest.raises(CommandError) as raised:
        pull_request.ensure_repository_cache("o", "r")
    assert_missing(raised.value, "git", "git をインストール")
    assert not (tmp_path / "home" / "repositories").exists()
