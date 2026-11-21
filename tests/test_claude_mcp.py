import json
import subprocess
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import test_security
from test_claude import fake_runner, make_invocation

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_INCOMPLETE_COVERAGE, EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.llm_runs import claude
from jigane.llm_runs.cost import Measured
from jigane.reviews.lifecycle import create_feature_review

project = test_security.project

PRIVATE = "fixture private diagnostics"
RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": PRIVATE,
    "num_turns": 2,
    "total_cost_usd": 0.012,
    "structured_output": {"findings": []},
}


def _init(status: str = "connected", *other_names: str) -> dict[str, object]:
    return {
        "type": "system",
        "subtype": "init",
        "mcp_servers": [
            {"name": "refs", "status": status, "error": PRIVATE},
            *({"name": name, "status": "connected"} for name in other_names),
        ],
    }


def _event(event_type: str, *blocks: dict[str, object]) -> dict[str, object]:
    return {"type": event_type, "message": {"content": blocks}}


def _stream(*events: dict[str, object]) -> str:
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def _call(call_id: str, server: str, tool: str = "ReadMcpResourceTool") -> dict[str, object]:
    return _event(
        "assistant",
        {
            "type": "tool_use",
            "id": call_id,
            "name": tool,
            "input": {"server": server, "uri": "fixture://document"},
        },
    )


def _reply(call_id: str, result: object, *, failed: bool = False) -> dict[str, object]:
    return {
        **_event("user", {"type": "tool_result", "tool_use_id": call_id, "is_error": failed}),
        "tool_use_result": result,
    }


_DOCUMENT = {"contents": [{"uri": "fixture://document", "text": PRIVATE}]}


def test_mcp_records_parallel_reads_in_selection_order(tmp_path: Path) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs", "other")
    )
    stdout = _stream(
        _init("connected", "other"),
        _call("one", "refs"),
        _call("two", "other"),
        _reply("two", _DOCUMENT),
        _reply("one", _DOCUMENT),
        _call("three", "other"),
        _reply("three", _DOCUMENT),
        RESULT,
    )
    record = claude.run(invocation, runner=fake_runner(stdout)).record
    assert record.mcp_read_servers == ("refs", "other")
    assert PRIVATE not in json.dumps(record.as_row())


@pytest.mark.parametrize(
    ("tool", "server", "result"),
    [
        ("ReadMcpResourceTool", "unexpected", _DOCUMENT),
        ("ListMcpResourcesTool", "refs", _DOCUMENT),
        ("mcp__refs__reference", "refs", _DOCUMENT),
        (
            "mcp__refs__reference",
            "refs",
            [{"type": "text", "text": f"[Resource from refs at fixture://document] {PRIVATE}"}],
        ),
        ("ReadMcpResourceTool", "refs", {"contents": []}),
        ("ReadMcpResourceTool", "refs", {"contents": [{"uri": "fixture://document"}]}),
        (
            "ReadMcpResourceTool",
            "refs",
            {"contents": [{"uri": "fixture://document", "text": " "}]},
        ),
        ("ReadMcpResourceTool", "refs", json.dumps(_DOCUMENT)),
    ],
)
def test_mcp_does_not_record_unconfirmed_bodies(
    tmp_path: Path, tool: str, server: str, result: object
) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    stdout = _stream(
        _init(),
        _call("one", server, tool),
        _reply("one", result),
        _call("pending", "refs"),
        _reply("unrelated", _DOCUMENT),
        RESULT,
        _reply("pending", _DOCUMENT),
    )
    assert claude.run(invocation, runner=fake_runner(stdout)).record.mcp_read_servers == ()


@pytest.mark.parametrize(("size", "reads"), [(0, ()), (7, ("refs",))])
def test_mcp_binary_result_requires_nonempty_bytes(
    tmp_path: Path, size: int, reads: tuple[str, ...]
) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    binary = {
        "uri": "fixture://document",
        "blobSavedTo": PRIVATE,
        "text": (
            f"[Resource from refs at fixture://document] "
            f"Binary content (unknown type, {size} bytes) saved to {PRIVATE}"
        ),
    }
    stdout = _stream(_init(), _call("one", "refs"), _reply("one", {"contents": [binary]}), RESULT)
    record = claude.run(invocation, runner=fake_runner(stdout)).record
    assert record.mcp_read_servers == reads
    assert PRIVATE not in json.dumps(record.as_row())


def test_mcp_duplicate_operation_ids_do_not_count(tmp_path: Path) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs", "other")
    )
    stdout = _stream(
        _init("connected", "other"),
        _call("one", "refs"),
        _call("one", "other"),
        _reply("one", _DOCUMENT),
        _call("two", "refs"),
        _reply("two", _DOCUMENT),
        _call("two", "other", "Read"),
        _call("three", "other"),
        _reply("three", _DOCUMENT),
        _reply("three", _DOCUMENT),
        _reply("orphan", _DOCUMENT),
        _call("orphan", "refs"),
        _reply("orphan", _DOCUMENT),
        RESULT,
    )
    assert claude.run(invocation, runner=fake_runner(stdout)).record.mcp_read_servers == ()


def test_mcp_result_shared_by_multiple_blocks_is_ambiguous(tmp_path: Path) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs", "other")
    )
    result = {
        **_event(
            "user",
            {"type": "tool_result", "tool_use_id": "one"},
            {"type": "tool_result", "tool_use_id": "two"},
        ),
        "tool_use_result": _DOCUMENT,
    }
    stdout = _stream(
        _init("connected", "other"), _call("one", "refs"), _call("two", "other"), result, RESULT
    )
    assert claude.run(invocation, runner=fake_runner(stdout)).record.mcp_read_servers == ()


@pytest.mark.parametrize("flagged_error", [False, True])
def test_mcp_failure_preserves_only_completed_reads(
    tmp_path: Path, *, flagged_error: bool
) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs", "other")
    )
    result = _DOCUMENT if flagged_error else {**_DOCUMENT, "error": PRIVATE}
    stdout = _stream(
        _init("connected", "other"),
        _call("one", "refs"),
        _call("two", "other"),
        _reply("one", _DOCUMENT),
        _reply("two", result, failed=flagged_error),
        RESULT,
    )
    with pytest.raises(claude.ClaudeInvocationError, match="MCP の呼び出しに失敗") as error:
        claude.run(invocation, runner=fake_runner(stdout, stderr=PRIVATE))
    assert error.value.record.mcp_read_servers == ("refs",)
    assert PRIVATE not in str(error.value) + json.dumps(error.value.record.as_row())


def test_mcp_timeout_keeps_completed_reads(tmp_path: Path) -> None:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = _stream(_init(), _call("one", "refs"), _reply("one", _DOCUMENT))
        raise subprocess.TimeoutExpired(command, 1, output=stdout.encode() + b'{"type":"u')

    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    with pytest.raises(claude.ClaudeInvocationError) as error:
        claude.run(invocation, runner=runner)
    assert error.value.kind is claude.FailureKind.TIMEOUT
    assert error.value.record.mcp_read_servers == ("refs",)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    ("status", "reason"), [("needs-auth", "認証が必要"), ("disabled", "無効")]
)
def test_mcp_reports_only_the_cli_status(tmp_path: Path, status: str, reason: str) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    runner = fake_runner(_stream(_init(status), RESULT), stderr=PRIVATE)
    with pytest.raises(claude.ClaudeInvocationError, match=reason) as error:
        claude.run(invocation, runner=runner)
    assert error.value.kind is claude.FailureKind.MCP_RESPONSE
    assert PRIVATE not in str(error.value)


@pytest.mark.parametrize("tool", ["mcp__refs__reference", *claude.MCP_RESOURCE_TOOLS, "Read"])
def test_mcp_errors_are_linked_to_the_tool_call(tmp_path: Path, tool: str) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    stdout = _stream(
        _init(),
        _event("assistant", {"type": "tool_use", "id": "first", "name": "mcp__refs__lookup"}),
        _event("assistant", {"type": "tool_use", "id": "second", "name": tool}),
        _event(
            "user",
            {"type": "tool_result", "tool_use_id": "other", "is_error": True},
            {"type": "tool_result", "tool_use_id": "second", "is_error": True, "content": PRIVATE},
            {"type": "tool_result", "tool_use_id": "first", "content": "valid\u2028text"},
        ),
        RESULT,
    )
    runner = fake_runner(stdout, stderr=PRIVATE)
    if tool == "Read":
        assert claude.run(invocation, runner=runner).structured_output == {"findings": []}
        return
    with pytest.raises(claude.ClaudeInvocationError, match="MCP の呼び出しに失敗") as error:
        claude.run(invocation, runner=runner)
    assert error.value.kind is claude.FailureKind.MCP_RESPONSE
    assert error.value.record.cost == Measured(0.012)
    assert error.value.record.is_error
    assert PRIVATE not in str(error.value)


@pytest.mark.parametrize("prefix", ["", PRIVATE + "\n", "[" * 201 + "]" * 201 + "\n"])
def test_valid_result_cannot_hide_missing_or_broken_mcp_events(
    tmp_path: Path, prefix: str
) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    events = (_init(), RESULT) if prefix else (RESULT,)
    with pytest.raises(claude.ClaudeInvocationError) as error:
        claude.run(invocation, runner=fake_runner(prefix + _stream(*events), stderr=PRIVATE))
    assert error.value.kind is claude.FailureKind.BAD_OUTPUT
    assert error.value.record.mcp_read_servers == ()
    assert error.value.record.cost == Measured(0.012)
    assert PRIVATE not in str(error.value)


def test_mcp_terminal_failure_keeps_its_budget_classification(tmp_path: Path) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    result = {
        **RESULT,
        "subtype": "error_max_budget_usd",
        "is_error": True,
        "terminal_reason": PRIVATE,
    }
    runner = fake_runner(_stream(result), returncode=1, stderr=PRIVATE)
    with pytest.raises(claude.ClaudeInvocationError) as error:
        claude.run(invocation, runner=runner)
    assert error.value.kind is claude.FailureKind.BUDGET_EXCEEDED
    assert error.value.record.cost == Measured(0.012)
    assert PRIVATE not in str(error.value)
    assert error.value.record.terminal_reason is None


def test_mcp_selected_server_missing_from_init_is_a_failure(tmp_path: Path) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs", "missing")
    )
    runner = fake_runner(_stream(_init(), RESULT))
    with pytest.raises(
        claude.ClaudeInvocationError, match="MCP 実行の結果を確認できませんでした"
    ) as error:
        claude.run(invocation, runner=runner)
    assert error.value.kind is claude.FailureKind.BAD_OUTPUT
    assert error.value.record.cost == Measured(0.012)


@pytest.mark.parametrize("tool_failed", [False, True])
def test_deep_tool_data_does_not_hide_the_mcp_result(tmp_path: Path, *, tool_failed: bool) -> None:
    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    data: dict[str, Any] = {"value": "} ] [ {"}
    for _ in range(210):
        data = {"children": [data]}
    stdout = _stream(
        _init(),
        _event(
            "assistant",
            {"type": "tool_use", "id": "mcp", "name": "mcp__refs__lookup", "input": data},
        ),
        _event(
            "user",
            {
                "type": "tool_result",
                "content": data,
                "tool_use_id": "mcp",
                "is_error": tool_failed,
            },
        ),
        RESULT,
    )
    runner = fake_runner(stdout)
    if tool_failed:
        with pytest.raises(claude.ClaudeInvocationError, match="MCP の呼び出しに失敗") as error:
            claude.run(invocation, runner=runner)
        assert error.value.kind is claude.FailureKind.MCP_RESPONSE
    else:
        assert claude.run(invocation, runner=runner).structured_output == {"findings": []}


def test_mcp_discards_stderr_and_handles_invalid_cli_utf8(tmp_path: Path) -> None:
    def runner(_command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["stderr"] == subprocess.DEVNULL
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "fixture")

    invocation = make_invocation(
        tmp_path, mcp_configs=(tmp_path / "mcp.json",), mcp_server_names=("refs",)
    )
    with pytest.raises(claude.ClaudeInvocationError, match="UTF-8") as error:
        claude.run(invocation, runner=runner)
    assert error.value.kind is claude.FailureKind.BAD_OUTPUT


@pytest.mark.usefixtures("selected_agent")
@pytest.mark.parametrize(
    ("failed_shards", "exit_code"),
    [(0, EXIT_OK), (1, EXIT_INCOMPLETE_COVERAGE), (2, EXIT_PROCESSING_FAILURE)],
)
def test_run_security_uses_claude_mcp_status_for_coverage(
    project: Path, capsys: pytest.CaptureFixture[str], failed_shards: int, exit_code: int
) -> None:
    review = create_feature_review(project, ["src"], "p")
    test_security.write_mcp(("refs", "server-sec", "mobile-sec"))

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[command.index("--output-format") + 1] == "stream-json"
        prompt = str(kwargs["input"])
        failed = failed_shards == 2 or (failed_shards == 1 and "担当のシャード: 1 / 2" in prompt)
        read_server = "server-sec" if failed else "refs"
        stdout = _stream(
            _init("failed" if failed else "connected", "server-sec", "mobile-sec"),
            _call("read", read_server),
            _reply("read", _DOCUMENT),
            RESULT,
        )
        return subprocess.CompletedProcess(command, 0, stdout, PRIVATE)

    name = str(review.id)
    args = ["run", name, "security", "--mcps", "refs", "server-sec", "mobile-sec"]
    invoke = partial(claude.run, runner=runner)
    code = main([*args, "--json"], invoke=invoke)
    assert code == exit_code
    report = capsys.readouterr()
    connection_failure = "MCP サーバに接続できませんでした"
    assert report.err.count(connection_failure) == min(failed_shards, 1)
    assert PRIVATE not in report.err + report.out
    body = json.loads(report.out)
    if failed_shards == 2:
        assert connection_failure in body["error"]["reason"]
        return
    expected_failures = ["mcp_response"] * failed_shards
    assert [failure["kind"] for failure in body["failures"]] == expected_failures
    selected = ["refs", "server-sec", "mobile-sec"]
    reads = ["refs", "server-sec"] if failed_shards else ["refs"]
    assert body["run"]["mcp_servers"] == selected
    assert body["run"]["mcp_read_servers"] == reads
    saved = (review.id.directory() / "runs.json").read_text(encoding="utf-8")
    assert PRIVATE not in saved
    assert main(["show", name, "security", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert shown["run"]["succeeded_shards"] == 2 - failed_shards
    assert [failure["kind"] for failure in shown["run"]["failures"]] == expected_failures
    assert shown["run"]["mcp_servers"] == selected
    assert shown["run"]["mcp_read_servers"] == reads
    expected = f"MCP: 選択 {', '.join(selected)}、本文取得の記録 {', '.join(reads)}"
    assert main(["show", name, "security"]) == EXIT_OK
    assert expected in capsys.readouterr().out
    assert main(args, invoke=invoke) == exit_code
    assert expected in capsys.readouterr().out


@pytest.mark.usefixtures("selected_agent")
def test_security_show_distinguishes_empty_mcp_reads(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    test_security.write_mcp(("refs",))
    name = str(review.id)
    invoke = partial(claude.run, runner=fake_runner(_stream(_init(), RESULT)))
    assert main(["run", name, "security", "--mcps", "refs", "--json"], invoke=invoke) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["run"]["mcp_read_servers"] == []
    assert main(["show", name, "security", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["run"]["mcp_read_servers"] == []
    assert main(["show", name, "security"]) == EXIT_OK
    assert "MCP: 選択 refs、本文取得の記録 なし" in capsys.readouterr().out
