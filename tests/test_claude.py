import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jigane.git.process import CommandError
from jigane.llm_runs.claude import (
    ClaudeInvocationError,
    FailureKind,
    Invocation,
    LaunchBudget,
    StoredInvocationRecord,
    build_command,
    classify_failure_text,
    estimate_input_tokens,
    normalize_path,
    parse_record,
    run,
    secret_read_denials,
)
from jigane.llm_runs.cost import LimitSubstitute, Measured

FIXTURES = Path(__file__).parent / "fixtures" / "claude"
SCHEMA = {"type": "object"}


def make_invocation(tmp_path: Path, **overrides: object) -> Invocation:
    values: dict[str, Any] = {
        "prompt": "review",
        "json_schema": SCHEMA,
        "working_directory": tmp_path,
        "budget": LaunchBudget(limit_usd=0.6, cli_usd=0.5),
        "model": "claude-sonnet-5-5",
    }
    values.update(overrides)
    return Invocation(**values)


def fake_runner(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return runner


def test_build_command_without_mcp(tmp_path: Path) -> None:
    command = build_command(make_invocation(tmp_path))
    assert command == [
        "claude",  # ast-grep-ignore: no-claude-launch-outside-llm-runs 起動ではなく期待する列
        "--restricted",
        "--settings",
        json.dumps(
            {
                "permissions": {
                    "deny": secret_read_denials(),
                    "blockReadsOutsideWorkingDirectories": True,
                }
            }
        ),
        "-p",
        "--tools",
        "Read",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SCHEMA, ensure_ascii=False),
        "--model",
        "claude-sonnet-5-5",
        "--max-budget-usd",
        "0.5",
        "--strict-mcp-config",
    ]


def test_build_command_settings_block_reads_outside_the_working_directory(tmp_path: Path) -> None:
    command = build_command(make_invocation(tmp_path))
    permissions = json.loads(command[command.index("--settings") + 1])["permissions"]
    assert permissions["blockReadsOutsideWorkingDirectories"] is True
    assert permissions["deny"] == secret_read_denials()


def test_build_command_passes_max_turns_only_when_set(tmp_path: Path) -> None:
    command = build_command(make_invocation(tmp_path, max_turns=12))
    assert command[command.index("--max-turns") + 1] == "12"
    assert "--max-turns" not in build_command(make_invocation(tmp_path))


def test_build_command_with_mcp(tmp_path: Path) -> None:
    invocation = make_invocation(
        tmp_path,
        mcp_config=tmp_path / "mcp.json",
        allowed_mcp_tools=("mcp__a__x", "mcp__a__y"),
    )
    command = build_command(invocation)
    assert command[-5:] == [
        "--strict-mcp-config",
        "--mcp-config",
        str(tmp_path / "mcp.json"),
        "--allowedTools",
        "mcp__a__x,mcp__a__y",
    ]


def test_parse_record_success() -> None:
    payload = json.loads((FIXTURES / "structured-success.json").read_text("utf-8"), strict=False)
    record = parse_record(payload, 0.5)
    assert record.input_tokens == 4
    assert record.output_tokens == 525
    assert record.cache_read_input_tokens == 2584
    assert record.cache_creation_input_tokens == 89585
    assert record.cost == Measured(0.3641148)
    assert record.duration_ms == 7978
    assert record.num_turns == 3
    assert record.is_error is False
    assert record.terminal_reason == "completed"
    assert record.permission_denials == 0


def test_as_row_does_not_write_a_session_id() -> None:
    assert "session_id" not in parse_record({"session_id": "abc-123"}, 0.5).as_row()


def test_from_row_discards_a_session_id_left_in_an_old_row() -> None:
    row = parse_record({}, 0.5).as_row()
    restored = StoredInvocationRecord.from_row({**row, "session_id": "abc-123"})
    assert restored == StoredInvocationRecord.from_row(row)
    assert not hasattr(restored, "session_id")


def test_parse_record_permission_denied() -> None:
    payload = json.loads((FIXTURES / "permission-denied.json").read_text("utf-8"), strict=False)
    assert parse_record(payload, 0.5).permission_denials == 1


def test_run_success(tmp_path: Path) -> None:
    stdout = (FIXTURES / "structured-success.json").read_text("utf-8")
    captured = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = run(make_invocation(tmp_path), runner=runner)
    assert result.structured_output is not None
    assert result.structured_output["findings"][0]["line"] == 2
    assert result.record.num_turns == 3
    assert captured["input"] == "review"
    assert "stdin" not in captured
    assert captured["cwd"] == tmp_path
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["encoding"] == "utf-8"
    assert isinstance(captured["env"], dict)


# ログインの認証を表す偽の値。子に残ることを確かめる
LOGIN_VALUE = "login-value"


def test_run_strips_write_credentials_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = (FIXTURES / "structured-success.json").read_text("utf-8")
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", LOGIN_VALUE)
    monkeypatch.setenv("ENABLE_CLAUDEAI_MCP_SERVERS", "true")
    captured: dict[str, Any] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout, "")

    run(make_invocation(tmp_path), runner=runner)
    assert "GH_TOKEN" not in captured["env"]
    assert captured["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == LOGIN_VALUE
    # claude.ai のコネクタは、利用者の環境に関わらず子の起動へ載せない
    assert captured["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"


def test_run_accepts_control_characters(tmp_path: Path) -> None:
    stdout = '{"is_error": false, "structured_output": {"a": "x\ty"}}'
    result = run(make_invocation(tmp_path), runner=fake_runner(stdout))
    assert result.structured_output == {"a": "x\ty"}


def test_run_nonzero_exit(tmp_path: Path) -> None:
    with pytest.raises(ClaudeInvocationError, match="boom"):
        run(make_invocation(tmp_path), runner=fake_runner("", 2, "boom"))


def test_run_nonzero_exit_reports_the_json_reason_when_stderr_is_empty(tmp_path: Path) -> None:
    stdout = json.dumps({"is_error": True, "subtype": "error_max_turns", "result": "too many"})
    with pytest.raises(ClaudeInvocationError, match="error_max_turns too many"):
        run(make_invocation(tmp_path), runner=fake_runner(stdout, 1, ""))


def test_run_nonzero_exit_adds_the_stdout_tail_to_stderr(tmp_path: Path) -> None:
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=fake_runner("x" * 600 + " last", 1, "warn"))
    message = str(raised.value)
    assert message.startswith("claude の終了コードが 1 でした: warn / …")
    assert message.endswith(" last")


def test_run_nonzero_exit_says_when_both_streams_are_empty(tmp_path: Path) -> None:
    with pytest.raises(ClaudeInvocationError, match="標準エラーと標準出力が空です"):
        run(make_invocation(tmp_path), runner=fake_runner("", 1, ""))


def test_run_not_json(tmp_path: Path) -> None:
    with pytest.raises(ClaudeInvocationError, match="JSON ではありません"):
        run(make_invocation(tmp_path), runner=fake_runner("not json"))


def test_run_is_error_carries_record(tmp_path: Path) -> None:
    stdout = json.dumps({"is_error": True, "num_turns": 1, "structured_output": {}})
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=fake_runner(stdout))
    assert raised.value.record is not None
    assert raised.value.record.num_turns == 1


def test_run_missing_structured_output(tmp_path: Path) -> None:
    stdout = json.dumps({"is_error": False, "result": "text"})
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=fake_runner(stdout))
    assert raised.value.record is not None


def test_normalize_path_absolute_inside(tmp_path: Path) -> None:
    inside = (tmp_path / "src" / "a.py").as_posix()
    assert normalize_path(inside, tmp_path) == "src/a.py"


def test_normalize_path_absolute_outside(tmp_path: Path) -> None:
    assert normalize_path("/definitely/elsewhere/a.py", tmp_path) is None


def test_normalize_path_relative(tmp_path: Path) -> None:
    assert normalize_path("./src//a.py", tmp_path) == "src/a.py"
    assert normalize_path("src/../b.py", tmp_path) == "b.py"


def test_normalize_path_escaping(tmp_path: Path) -> None:
    assert normalize_path("../a.py", tmp_path) is None


def test_estimate_input_tokens() -> None:
    assert estimate_input_tokens(0) == 0
    assert estimate_input_tokens(3) == 1
    assert estimate_input_tokens(4) == 2


def test_build_command_with_mcp_and_no_allowed_tools(tmp_path: Path) -> None:
    invocation = make_invocation(tmp_path, mcp_config=tmp_path / "mcp.json")
    command = build_command(invocation)
    assert command[-3:] == [
        "--strict-mcp-config",
        "--mcp-config",
        str(tmp_path / "mcp.json"),
    ]
    assert "--allowedTools" not in command


def test_normalize_path_through_symlinked_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "src").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    # CLI は symlink を解決した表記で返すことがある
    assert normalize_path(f"{real.as_posix()}/src/a.py", link) == "src/a.py"
    assert normalize_path(f"{link.as_posix()}/src/a.py", link) == "src/a.py"


def test_build_command_drops_meta_schema_keys(tmp_path: Path) -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "finding",
        "type": "object",
    }
    command = build_command(make_invocation(tmp_path, json_schema=schema))
    passed = json.loads(command[command.index("--json-schema") + 1])
    assert passed == {"type": "object"}


def test_build_command_excludes_prompt(tmp_path: Path) -> None:
    command = build_command(make_invocation(tmp_path, prompt="UNIQUE-PROMPT-TEXT"))
    assert not any("UNIQUE-PROMPT-TEXT" in argument for argument in command)


def test_run_wraps_timeout_in_invocation_error(tmp_path: Path) -> None:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 1)

    with pytest.raises(ClaudeInvocationError, match="タイムアウト"):
        run(make_invocation(tmp_path), runner=runner, timeout_seconds=1)


@pytest.mark.parametrize("path", ["C:\\x\\a.py", "C:/x/a.py", "\\\\server\\share\\a.py"])
def test_normalize_path_windows_absolute_outside(tmp_path: Path, path: str) -> None:
    assert normalize_path(path, tmp_path) is None


def test_missing_claude_executable_is_missing_tool(tmp_path: Path) -> None:
    invocation = make_invocation(tmp_path)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file", command[0])

    with pytest.raises(CommandError) as raised:
        run(invocation, runner=runner)
    assert raised.value.missing_executable


def test_missing_working_directory_is_not_reported_as_a_launch_failure(tmp_path: Path) -> None:
    invocation = make_invocation(tmp_path / "nowhere")

    def runner(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory")

    with pytest.raises(FileNotFoundError):
        run(invocation, runner=runner)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("API Error: 529 overloaded_error", FailureKind.OVERLOADED),
        ("429 rate_limit_error", FailureKind.RATE_LIMIT),
        ("API Error: 429", FailureKind.RATE_LIMIT),
        ("HTTP 503", FailureKind.SERVER_ERROR),
        ("status: 500", FailureKind.SERVER_ERROR),
        ("502 Bad Gateway", FailureKind.SERVER_ERROR),
        ("429 Too Many Requests", FailureKind.RATE_LIMIT),
        ("Request timed out", FailureKind.TIMEOUT),
        ("read ECONNRESET", FailureKind.CONNECTION),
        ("service unavailable", FailureKind.SERVER_ERROR),
    ],
)
def test_failure_text_is_classified_in_one_place(text: str, kind: FailureKind) -> None:
    assert classify_failure_text(text) is kind


@pytest.mark.parametrize(
    "text",
    [
        "cost 0.4291 USD",
        "cost 0.429 USD",
        "total 1.529 USD",
        "status 4290",
        "error 5000",
        "used 429 tokens",
        "$529 charged",
    ],
)
def test_amounts_and_decimals_are_not_http_statuses(text: str) -> None:
    assert classify_failure_text(text) is FailureKind.OTHER


@pytest.mark.parametrize(
    ("kind", "transient"),
    [
        (FailureKind.TIMEOUT, True),
        (FailureKind.CONNECTION, True),
        (FailureKind.RATE_LIMIT, True),
        (FailureKind.OVERLOADED, True),
        (FailureKind.SERVER_ERROR, True),
        (FailureKind.BUDGET_EXCEEDED, False),
        (FailureKind.TURNS_EXCEEDED, False),
        (FailureKind.BAD_OUTPUT, False),
        (FailureKind.OTHER, False),
    ],
)
def test_transient_follows_the_kind_not_the_cause(kind: FailureKind, *, transient: bool) -> None:
    # cause が過負荷を名乗っても、種類が決める
    error = ClaudeInvocationError("529 overloaded", parse_record({}, 0.5), kind)
    assert error.transient is transient


def test_kind_defaults_to_other() -> None:
    assert ClaudeInvocationError("boom", parse_record({}, 0.5)).kind is FailureKind.OTHER


def test_timeout_is_decided_at_the_throw_site(tmp_path: Path) -> None:
    def runner(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("claude", 1)

    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=runner)
    assert raised.value.kind is FailureKind.TIMEOUT
    # 金額が報告されない失敗は、起動の上限（CLI に渡す cli_usd でなく budget.limit_usd）で代える
    assert raised.value.record.cost == LimitSubstitute(0.6)


def test_nonzero_exit_kind_comes_from_the_reason(tmp_path: Path) -> None:
    runner = fake_runner("", returncode=1, stderr="API Error: 529 overloaded_error")
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=runner)
    assert raised.value.kind is FailureKind.OVERLOADED
    runner = fake_runner("", returncode=1, stderr="cost 0.4291 USD")
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=runner)
    assert raised.value.kind is FailureKind.OTHER


@pytest.mark.parametrize(
    ("subtype", "kind"),
    [
        ("error_max_budget_usd", FailureKind.BUDGET_EXCEEDED),
        ("error_max_turns", FailureKind.TURNS_EXCEEDED),
    ],
)
def test_is_error_limits_are_decided_by_subtype(
    tmp_path: Path, subtype: str, kind: FailureKind
) -> None:
    stdout = json.dumps({"is_error": True, "subtype": subtype, "result": "429 rate limit"})
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=fake_runner(stdout))
    assert raised.value.kind is kind
    assert not raised.value.transient


@pytest.mark.parametrize(
    ("subtype", "kind"),
    [
        ("error_max_budget_usd", FailureKind.BUDGET_EXCEEDED),
        ("error_max_turns", FailureKind.TURNS_EXCEEDED),
    ],
)
def test_nonzero_exit_limits_are_decided_by_subtype(
    tmp_path: Path, subtype: str, kind: FailureKind
) -> None:
    stdout = json.dumps({"is_error": True, "subtype": subtype, "result": "429 rate limit"})
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=fake_runner(stdout, returncode=1))
    assert raised.value.kind is kind


def test_nonzero_exit_with_unreadable_stdout_falls_back_to_text(tmp_path: Path) -> None:
    runner = fake_runner("API Error: 529 overloaded_error", returncode=1)
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=runner)
    assert raised.value.kind is FailureKind.OVERLOADED


def test_bad_output_kinds(tmp_path: Path) -> None:
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=fake_runner("not json"))
    assert raised.value.kind is FailureKind.BAD_OUTPUT
    stdout = json.dumps({"is_error": False})
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path), runner=fake_runner(stdout))
    assert raised.value.kind is FailureKind.BAD_OUTPUT


def test_run_is_error_carries_the_reason(tmp_path: Path) -> None:
    stdout = json.dumps(
        {"is_error": True, "subtype": "success", "result": "API Error: 529 overloaded"}
    )
    with pytest.raises(ClaudeInvocationError, match="529 overloaded") as raised:
        run(make_invocation(tmp_path), runner=fake_runner(stdout))
    assert raised.value.transient


def test_claude_binary_defaults_to_claude_when_unset_or_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JIGANE_CLAUDE_BINARY", raising=False)
    assert build_command(make_invocation(tmp_path))[0] == "claude"
    monkeypatch.setenv("JIGANE_CLAUDE_BINARY", "")
    assert build_command(make_invocation(tmp_path))[0] == "claude"


def test_claude_binary_is_replaced_by_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIGANE_CLAUDE_BINARY", "/somewhere/fake")
    assert build_command(make_invocation(tmp_path))[0] == "/somewhere/fake"
