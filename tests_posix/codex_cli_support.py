"""合成差分の試験で使う偽の Codex CLI と構造化応答。"""

import inspect
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from jigane.storage.agent_selection import Agent, save_agent

FAKE_CODEX = """\
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
assert Path.cwd() == Path(os.environ["HOME"]) == Path(os.environ["TMPDIR"])
assert all(name not in os.environ for name in (
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "GITHUB_TOKEN",
    "HTTPS_PROXY"
))
login_home = Path(os.environ["CODEX_HOME"])
assert login_home == Path({marker!r}).with_name("codex-login")
assert 'cli_auth_credentials_store="auto"' in args
if args[:2] == ["login", "status"]:
    assert not sys.stdin.read()
    assert not any(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr))
    Path({marker!r}).with_name("login-checked").touch()
    status = (login_home / "login-mode").read_text()
    print(status, file=sys.stderr)
    sys.exit(1 if status == "Not logged in" else 0)
assert args[0] == "exec" and args[-1] == "-"
assert not any(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr))
assert "--ephemeral" in args and "--ignore-user-config" in args
assert "--ignore-rules" in args and "--strict-config" in args
assert args[args.index("--sandbox") + 1] == "read-only"
disabled = {arg for flag, arg in zip(args, args[1:]) if flag == "--disable"}
assert {"shell_tool", "shell_snapshot", "view_image", "multi_agent", "goals", "apps", "plugins",
        "remote_plugin", "hooks", "browser_use", "computer_use", "unbounded_connection_retries",
        "respect_system_proxy", "system_proxy_fallback"} <= disabled
assert "mcp_servers={}" in args
assert 'project_doc_max_bytes=0' in args and 'skills.include_instructions=false' in args
assert not Path("sample.py").exists()
provider = next(arg for arg in args if arg.startswith("model_providers."))
assert 'request_max_retries=0, stream_max_retries=0' in provider
assert 'base_url="https://chatgpt.com/backend-api/codex"' in provider
assert "requires_openai_auth=true" in provider and "env_key" not in provider
prompt = sys.stdin.read()
assert "sample.py" in prompt
schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
finding = schema["properties"]["findings"]["items"]
assert set(finding["properties"]) == set(finding["required"])
with Path({marker!r}).open("a") as trace:
    trace.write("called\\n")
{response}
"""


def fake_chatgpt_login(directory: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    login_home = directory / "codex-login"
    login_home.mkdir()
    (login_home / "login-mode").write_text("Logged in using ChatGPT", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(login_home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return login_home


@pytest.fixture
def codex_trace(synthetic_diff: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trace = synthetic_diff / "roles"
    binary = synthetic_diff / "codex"
    binary.write_text(
        f"#!{sys.executable}\nfrom __future__ import annotations\n"
        + FAKE_CODEX.replace(
            'assert "sample.py" in prompt',
            'assert "攻撃手順の生成は行わない" in prompt',
        )
        .replace('finding = schema["properties"]["findings"]["items"]', "finding = schema")
        .replace('    trace.write("called\\n")', "    pass")
        .replace("{marker!r}", repr(str(trace)))
        .replace(
            "{response}",
            (inspect.getsource(_group_response) + _CLI_GROUP_OUTPUT).replace(
                "{trace}", repr(str(trace))
            ),
        ),
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", f"{synthetic_diff}{os.pathsep}{os.environ['PATH']}")
    fake_chatgpt_login(synthetic_diff, monkeypatch)
    save_agent(Agent.CODEX)
    return trace


def _group_response(schema: dict[str, Any], prompt: str) -> tuple[str, dict[str, Any]]:
    output: dict[str, Any]
    props = schema["properties"]
    evidence = [{"path": "sample.py", "start_line": 2, "end_line": 2}]
    if "findings" in props:
        assert "sample.py" in prompt
        field = props["findings"]["items"]["properties"]["viewpoint"]
        viewpoints = field.get("enum", [field.get("const")])
        if "D1" in viewpoints or "S-STORAGE" in viewpoints:
            role = "discovery"
            is_security = "S-STORAGE" in viewpoints
            output = {
                "findings": [
                    {
                        "severity": "warning",
                        "viewpoint": "S-STORAGE" if is_security else "D1",
                        "claim": "Sample value changed",
                        "body": "Check the expected value.",
                        "evidence": evidence,
                        "declaration_ref": None,
                        "judgment_region": None,
                        "references": ["MASVS-STORAGE-1"] if is_security else [],
                        "in_scope": True,
                        "scope_reason": "Changed line",
                        "from_concern": None,
                        "disclosed_at": None,
                    }
                ]
            }
        elif viewpoints[0] in {"P1", "P2", "P3", "P4", "P5"}:
            role = "principle"
            output = {"findings": []}
        else:
            role = "diagram"
            output = {"findings": [], "merged": [], "discarded": []}
    elif "themes" in props:
        role = "themes"
        output = {"themes": []}
    else:
        assert "return 2" in prompt
        fields = props["items"]["items"]["properties"]
        if "challenge" in fields:
            role = "rebuttal"
            item: dict[str, Any] = {"challenge": "Check the supplied line.", "checked": evidence}
        else:
            role = "judge"
            item = {
                "verdict": "kept",
                "reason": "The supplied line supports the finding.",
                "recheck": evidence,
                "verified": True,
                "disclosed_at": None,
                "tool_claim": {"relies": False, "basis": "none", "version": ""},
            }
        output = {"items": [{"index": 0, **item}]}
    return role, output


_CLI_GROUP_OUTPUT = """\
role, output = _group_response(schema, prompt)
expected = Path({trace}).with_name("expected-prompts.json")
model = args[args.index("--model") + 1]
if expected.exists():
    assert prompt == json.loads(expected.read_text(encoding="utf-8"))[role]
if role == "principle":
    field = schema["properties"]["findings"]["items"]["properties"]["viewpoint"]
    viewpoint = field.get("enum", [field.get("const")])[0]
    assert model == {"P1": "gpt-6-luna", "P2": "gpt-6-luna", "P3": "gpt-6.1-terra",
                     "P4": "gpt-6.2-sol", "P5": "gpt-6.1-terra"}[viewpoint]
    assert 'model_reasoning_effort="high"' in args
elif role == "diagram":
    assert model == "gpt-6.2-sol"
    assert 'model_reasoning_effort="high"' in args
else:
    assert model == "gpt-6-luna"
    effort = "high" if role == "discovery" else "low"
    assert f'model_reasoning_effort="{effort}"' in args
assert 'service_tier="default"' in args
with Path({trace}).open("a") as trace:
    trace.write(role + "\\n")
Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(output))
override = Path({trace}).with_name("response-model")
print(json.dumps({"type": "turn.completed",
                  "server_model": override.read_text() if override.exists() else model, "usage": {
    "input_tokens": 12, "cached_input_tokens": 3,
    "output_tokens": 261 if role in ("diagram", "principle") else 5
}}))
"""
