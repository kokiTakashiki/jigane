"""合成資料サーバで Codex の MCP 選択・利用・記録の経路を確かめる。"""

import json
import sys
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest

from jigane.commands import main
from jigane.git import process
from jigane.llm_runs import codex_mcp
from jigane.skills.mcp import CODEX_MCP_DIRECTORY, McpSelection, SecurityConfigError
from jigane.storage.store import data_home

PRIVATE = "synthetic-reference-secret"


def _register(fixture: Path, name: str, home: Path | None = None) -> Path:
    reference = fixture.with_name(f"trusted {name}")
    reference.write_text(f"#!{sys.executable}\n", encoding="utf-8")
    reference.chmod(0o700)
    registration = (data_home() if home is None else home) / CODEX_MCP_DIRECTORY
    registration.mkdir(mode=0o700, exist_ok=True)
    (registration / name).symlink_to(reference)
    return reference.resolve()


_CHECK_REFERENCE_RUNTIME = """\
import tomllib

assert "--ignore-user-config" in args
assert "tool_call_mcp_elicitation" in args
assert all("synthetic-reference-secret" not in arg for arg in args)
override = next(arg for arg in args if arg.startswith("mcp_servers="))
servers = tomllib.loads(override)["mcp_servers"]
assert list(servers) == ["local-refs", "other-refs"]
catalog_arg = next(arg for arg in args if arg.startswith("model_catalog_json="))
catalog = Path(tomllib.loads(catalog_arg)["model_catalog_json"])
model = json.loads(catalog.read_text())["models"][0]
assert model["slug"] == args[args.index("--model") + 1]
assert model["tool_mode"] == "direct" and model["shell_type"] == "disabled"
assert model["use_responses_lite"] and not model["experimental_supported_tools"]
tools_config = tomllib.loads(next(arg for arg in args if arg.startswith("tools=")))["tools"]
assert set(tools_config) == {"experimental_request_user_input", "update_plan"}
assert all(tool == {"enabled": False} for tool in tools_config.values())
for name, entry in servers.items():
    assert entry == {
        "command": str(Path(__file__).with_name("trusted " + name).resolve()),
        "enabled_tools": [],
        "required": True,
    }
assert not list(Path.cwd().glob("mcp-*"))
"""

_MCP_EVENTS = """\
assert "選択された MCP の資料一覧" in prompt and "合成の基準資料" in prompt
resource = {"uri": "fixture://rule", "text": "synthetic rule"}
for server, tool, status, contents in [
    ("local-refs", "read_mcp_resource", "completed", [resource]),
    ("unselected", "read_mcp_resource", "failed", [resource]),
    ("unselected", "read_mcp_resource", "completed", [resource]),
    ("other-refs", "list_mcp_resources", "completed", [resource]),
    ("other-refs", "read_mcp_resource", "completed", []),
    ("other-refs", "read_mcp_resource", "completed",
     [{"uri": "fixture://rule"}, {"uri": "fixture://rule", "text": ""},
      {"uri": "fixture://rule", "blob": ""}]),
]:
    body = {"server": server, "contents": contents}
    item = {"type": "mcp_tool_call", "tool": tool, "server": server, "status": status,
            "result": {"content": [{"type": "text", "text": json.dumps(body)}]}}
    print(json.dumps({"type": "item.completed", "item": item}))
"""


@pytest.mark.parametrize("failure", ["startup", "read"])
def test_codex_reports_upstream_mcp_failures_without_diagnostics(
    codex_trace: Path, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    _register(codex_trace, "local-refs")
    binary = codex_trace.with_name("codex")
    startup_message = f"{codex_mcp._STARTUP_DIAGNOSTIC} local-refs: {PRIVATE}"
    body = (
        f"print({startup_message!r}, file=sys.stderr)\nsys.exit(1)"
        if failure == "startup"
        else "print("
        + repr(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "local-refs",
                        "tool": "read_mcp_resource",
                        "status": "failed",
                        "error": {"message": PRIVATE},
                    },
                }
            )
        )
        + ")"
    )
    binary.write_text(
        binary.read_text(encoding="utf-8").replace('assert "mcp_servers={}" in args', body),
        encoding="utf-8",
    )
    review = "octo/synthetic@sample"
    capsys.readouterr()
    assert main(["run", review, "security", "--mcps", "local-refs", "--json"]) == 2
    captured = capsys.readouterr()
    reason = codex_mcp.STARTUP_FAILURE if failure == "startup" else codex_mcp.READ_FAILURE
    assert reason in captured.err
    assert PRIVATE not in captured.out + captured.err


def test_codex_rejects_writable_native_executable(codex_trace: Path) -> None:
    reference = _register(codex_trace, "local-refs")
    reference.chmod(0o777)
    with pytest.raises(SecurityConfigError, match="実行ファイルを確認できません"):
        McpSelection.load(("local-refs",), codex_cli=True)


def test_codex_uses_selected_resources_and_records_reads(
    codex_trace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launch = process.run_codex_resolved

    def checked_fake(args: list[str], **kwargs: Any) -> CompletedProcess[str]:  # noqa: ANN401 起動引数
        result = launch(args, **kwargs)
        assert result.returncode == 0, result.stderr
        return result

    monkeypatch.setattr(process, "run_codex_resolved", checked_fake)
    registrations = codex_trace.with_name("mcp registry")
    registrations.mkdir(mode=0o700)
    monkeypatch.setenv("JIGANE_MCP_HOME", str(registrations))
    for name in ("local-refs", "other-refs", "unselected"):
        _register(codex_trace, name, registrations)
    binary = codex_trace.with_name("codex")
    source = binary.read_text(encoding="utf-8")
    binary.write_text(
        source.replace('assert "mcp_servers={}" in args', _CHECK_REFERENCE_RUNTIME).replace(
            "role, output = _group_response(schema, prompt)",
            _MCP_EVENTS + "role, output = _group_response(schema, prompt)",
        ),
        encoding="utf-8",
    )
    legacy = data_home() / "mcp.json"
    legacy.write_text(PRIVATE, encoding="utf-8")
    monkeypatch.setenv("PARENT_SECRET", "synthetic-parent-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    guide = codex_trace.with_name("guide.toml")
    guide.write_text(
        '[mcps.local-refs]\nuse_for = "合成の基準資料"\n[mcps.other-refs]\nuse_for = "補足"\n',
        encoding="utf-8",
    )
    args = [
        "octo/synthetic@sample",
        "security",
        "--mcps",
        "local-refs",
        "other-refs",
        "--mcp-guide",
        str(guide),
        "--json",
    ]
    capsys.readouterr()
    assert main(["estimate", *args]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["cli_auth"] == "chatgpt_login"
    assert estimate["noninteractive_run_available"] is False
    assert estimate["noninteractive_unavailable_reason"] == "mcp"
    assert estimate["expected"]["total_usd"] is None
    assert estimate["mcp_servers"] == ["local-refs", "other-refs"]
    assert main(["debug", "prompt", *args]) == 0
    prompt = json.loads(capsys.readouterr().out)["prompts"][0]
    assert [prompt["bytes"]] == estimate["prompt_bytes"]
    assert "リポジトリのファイルや外部の参照先へ" not in prompt["text"]
    assert "合成の基準資料" in prompt["text"]
    assert main(["debug", "run", *args, "--yes", "--max-usd", "0.01"]) == 64
    assert "--mcps と --mcp-guide" in capsys.readouterr().err
    assert not codex_trace.exists()

    assert main(["run", *args]) == 0
    assert not (data_home() / CODEX_MCP_DIRECTORY).exists()
    captured = capsys.readouterr()
    assert "保存された ChatGPT ログイン" in captured.err
    assert "使う MCP: local-refs, other-refs" in captured.err
    assert PRIVATE not in captured.out + captured.err
    result = json.loads(captured.out)
    assert result["run"]["mcp_servers"] == ["local-refs", "other-refs"]
    assert result["run"]["mcp_read_servers"] == ["local-refs"]
    invocations = result["run"]["invocations"]
    assert [row["role"] for row in invocations] == ["discovery", "rebuttal", "judge"]
    assert all(row["mcp_read_servers"] == ["local-refs"] for row in invocations)
    assert result["totals"]["usage_priced_cost_usd"] > 0
    _assert_mcp_views(args, result["run"], capsys)


def _assert_mcp_views(
    args: list[str], run: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    review = args[0]
    label = "MCP: 選択 local-refs, other-refs、本文取得の記録 local-refs"
    assert main(["run", *args[:-1]]) == 0
    assert label in capsys.readouterr().out
    assert main(["usage", review]) == 0
    assert label in capsys.readouterr().out
    assert main(["show", review, "security"]) == 0
    assert label in capsys.readouterr().out
    assert main(["show", review, "security", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)["run"]
    assert shown["mcp_servers"] == run["mcp_servers"]
    assert shown["mcp_read_servers"] == run["mcp_read_servers"]
