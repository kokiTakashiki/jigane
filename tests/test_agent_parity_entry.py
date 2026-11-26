"""A1 の保存済み選択と起動関数の注入を、CLI の実行入口で検査する。"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fakes import RECORD

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands import run
from jigane.llm_runs import claude, codex
from jigane.llm_runs.invocation import InvocationResult
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.storage.agent_selection import AGENT_FILE, Agent, save_agent
from jigane.storage.store import data_home


@pytest.mark.parametrize("agent", list(Agent))
@pytest.mark.parametrize("override", [False, True], ids=["default", "injected"])
@pytest.mark.parametrize(
    "command",
    [["run"], ["debug", "run", "--yes", "--max-usd", "0.99"]],
    ids=["run", "debug-run"],
)
def test_saved_agent_uses_selected_launch(
    agent: Agent,
    command: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    override: bool,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    review = create_feature_review(tmp_path, [source.name], name="entry")
    assert main(["select-agent", agent.value]) == 0
    capsys.readouterr()
    selection = data_home() / AGENT_FILE
    saved = selection.read_bytes()
    assert json.loads(saved) == {"agent": agent.value}

    result = InvocationResult({"findings": []}, RECORD)
    injected = Mock(return_value=result)
    claude_launch = Mock(return_value=result)
    claude_command = Mock(side_effect=AssertionError("unexpected Claude Code launch"))
    codex_launch = Mock(return_value=result)
    monkeypatch.setattr(claude, "build_command", claude_command)
    monkeypatch.setattr(claude, "run", claude_launch)
    monkeypatch.setattr(codex, "run", codex_launch)
    monkeypatch.setattr(codex, "check_chatgpt_login", Mock())
    monkeypatch.setattr(run, "codex_supported", lambda: True)

    options = {"invoke": injected} if override else {}
    status = main(
        [*command, str(review.id), "micro", "--json"],
        is_terminal=lambda: True,
        read_answer=lambda _: "y",
        **options,
    )

    captured = capsys.readouterr()
    assert status == 0, captured.err
    default_launch = claude_launch if agent is Agent.CLAUDE else codex_launch
    expected_launch = injected if override else default_launch
    expected_launch.assert_called_once()
    assert sum(launch.call_count for launch in (injected, claude_launch, codex_launch)) == 1
    claude_command.assert_not_called()
    assert json.loads(captured.out)["totals"]["invocation_count"] == 1
    assert selection.read_bytes() == saved


@pytest.mark.parametrize("agent", list(Agent))
@pytest.mark.parametrize(
    "command",
    [["run"], ["debug", "run", "--yes", "--max-usd", "0.99"]],
    ids=["run", "debug-run"],
)
@pytest.mark.parametrize(
    ("workspace", "resolved_workspace"),
    [
        (r"\\server\share\repo", None),
        (r"\\wsl$\Ubuntu\home\repo", None),
        (r"\\wsl.localhost\Ubuntu\home\repo", None),
        ("//server/share/repo", None),
        (r"\\?\uNc\server\share\repo", None),
        (r"\\.\UNC\server\share\repo", None),
        (r"Z:\repo", r"\\server\share\repo"),
    ],
)
def test_native_windows_unc_workspace_stops_before_launch(
    agent: Agent,
    command: list[str],
    workspace: str,
    resolved_workspace: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    save_agent(agent)
    review = Mock(spec=Review, repository_root=workspace)
    monkeypatch.setattr(run, "resolve_review", Mock())
    monkeypatch.setattr(run, "load_review", Mock(return_value=review))
    monkeypatch.setattr(run, "is_windows", lambda: True)
    monkeypatch.setattr(run, "codex_supported", lambda: False)
    path = Mock()
    path.return_value.resolve.return_value = resolved_workspace
    monkeypatch.setattr(run, "Path", path)
    work = Mock(side_effect=AssertionError("unexpected workspace access or launch"))
    monkeypatch.setattr(run, "read_docs_for_review", work)
    monkeypatch.setattr(claude, "run", work)
    monkeypatch.setattr(codex, "run", work)
    monkeypatch.setattr(codex, "check_chatgpt_login", work)

    status = main([*command, "entry", "micro", "--json"], read_answer=work)

    captured = capsys.readouterr()
    assert status == EXIT_PROCESSING_FAILURE
    error = json.loads(captured.out)["error"]
    assert error["kind"] == "unsupported_review_workspace"
    assert "UNC パス" in error["reason"]
    assert "ローカルドライブに置いたリポジトリ" in error["action"]
    assert "WSL 内で jigane を実行" in error["action"]
    assert error["reason"] in captured.err
    assert error["action"] in captured.err
    work.assert_not_called()
    if resolved_workspace is None:
        path.assert_not_called()
    else:
        path.assert_called_once_with(workspace)
        path.return_value.resolve.assert_called_once_with()


@pytest.mark.parametrize(
    ("native_windows", "workspace"),
    [
        (True, r"C:\repo"),
        (True, r"\\?\C:\repo"),
        (True, r"\\.\C:\repo"),
        (False, "/opt/reviewer/repo"),
        (False, "/home/reviewer/repo"),
        (False, "//local/repo"),
    ],
)
def test_run_workspace_check_allows_local_paths(
    workspace: str, monkeypatch: pytest.MonkeyPatch, *, native_windows: bool
) -> None:
    review = Mock(spec=Review, repository_root=workspace)
    monkeypatch.setattr(run, "resolve_review", Mock())
    monkeypatch.setattr(run, "load_review", Mock(return_value=review))
    monkeypatch.setattr(run, "is_windows", lambda: native_windows)
    path = Mock()
    path.return_value.resolve.return_value = workspace
    monkeypatch.setattr(run, "Path", path)

    assert run._load_review_for_run("entry") is review
    if native_windows:
        path.assert_called_once_with(workspace)
        path.return_value.resolve.assert_called_once_with()
    else:
        path.assert_not_called()
