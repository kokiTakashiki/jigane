"""偽の実行ファイルを使い、通常の run で短い空出力の境界を確かめる。"""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fakes import MacroFake

from jigane.commands import main
from jigane.groups.policy_runs import PolicyRole
from jigane.llm_runs import claude
from jigane.skills import policy_principles
from jigane.skills.policy_principle_shapes import Shape
from jigane.storage.agent_selection import Agent, save_agent
from tests_posix.codex_cli_support import fake_chatgpt_login

FAKE_AGENT = """\
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.308 (Claude Code)")
    sys.exit()
if args[:2] == ["login", "status"]:
    print("Logged in using ChatGPT")
    sys.exit()
sys.stdin.read()
codex = args[0] == "exec"
schema = (
    json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
    if codex
    else json.loads(args[args.index("--json-schema") + 1])
)
properties = schema["properties"]
if short_diagram:
    tokens = 99 if "merged" in properties else 261
output = {key: [] for key in ("findings", "merged", "discarded") if key in properties}
if with_finding:
    field = properties["findings"]["items"]["properties"]["viewpoint"]
    viewpoint = field.get("enum", [field.get("const")])[0]
    output["findings"].append({
        "viewpoint": viewpoint,
        "severity": "nit",
        "claim": "Sample finding",
        "body": "Check the return value.",
        "evidence": [{"path": "sample.py", "start_line": 2, "end_line": 2}],
        "declaration_ref": None,
        "judgment_region": None,
        "references": ["MASVS-STORAGE-1"] if viewpoint.startswith("S-") else [],
        "in_scope": True,
        "scope_reason": "Changed line",
    })
usage = {"input_tokens": 12}
if tokens is not None:
    usage["output_tokens"] = tokens
if codex:
    if type(tokens) is int:
        usage["reasoning_output_tokens"] = tokens
    Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(output))
    print(json.dumps({"type": "turn.completed", "usage": usage}))
else:
    print(json.dumps({"structured_output": output, "usage": usage}))
"""


@pytest.mark.parametrize("agent", [Agent.CLAUDE, Agent.CODEX])
@pytest.mark.parametrize("group", ["macro", "micro", "security"])
@pytest.mark.parametrize(
    "case",
    [
        (0, False, 3),
        (99, False, 3),
        (100, False, 0),
        (261, False, 0),
        (15, True, 0),
        (None, False, 0),
        ("99", False, 0),
    ],
)
def test_review_output_boundary(
    synthetic_diff: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agent: Agent,
    group: str,
    case: tuple[int | str | None, bool, int],
) -> None:
    tokens, with_finding, code = case
    _select_fake_agent(
        synthetic_diff, monkeypatch, agent, tokens=tokens, with_finding=with_finding
    )
    capsys.readouterr()

    status = main(["run", "octo/synthetic@sample", group])
    output = capsys.readouterr()
    assert status == code, output.err
    warning = f"警告: 出力が {tokens} トークン"
    if code == 3:
        assert warning in output.err
        assert "もう一度実行するか、別のエージェントで確かめてください" in output.err
        assert "の指摘: 0 件" not in output.out
    else:
        assert "指摘を得られず、レビューが欠けています" not in output.err
        assert ("の指摘: 0 件" in output.out) is not with_finding


def _select_fake_agent(
    synthetic_diff: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: Agent,
    *,
    tokens: int | str | None = None,
    with_finding: bool = False,
    short_diagram: bool = False,
) -> None:
    binary = synthetic_diff / str(agent)
    binary.write_text(
        f"#!{sys.executable}\ntokens = {tokens!r}\nwith_finding = {with_finding!r}\n"
        f"short_diagram = {short_diagram!r}\n" + FAKE_AGENT,
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", f"{synthetic_diff}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("JIGANE_CLAUDE_BINARY", str(binary))
    if agent is Agent.CODEX:
        fake_chatgpt_login(synthetic_diff, monkeypatch)
    save_agent(agent)


@pytest.mark.parametrize("agent", [Agent.CLAUDE, Agent.CODEX])
def test_macro_checks_each_parallel_launch(
    synthetic_diff: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agent: Agent,
) -> None:
    monkeypatch.setattr(policy_principles, "present_shapes", lambda *_: {Shape.FIELD_COPIES})
    _select_fake_agent(synthetic_diff, monkeypatch, agent, short_diagram=True)
    capsys.readouterr()
    assert main(["run", "octo/synthetic@sample", "macro", "--json"]) == 3
    output = capsys.readouterr()
    report = json.loads(output.out)
    [failure] = report["failures"]
    assert failure["kind"] == "short_empty_output"
    assert "警告: 出力が 99 トークン" in output.err
    [successful] = report["run"]["invocations"]
    assert successful["output_tokens"] == 261


@pytest.mark.usefixtures("synthetic_diff", "selected_agent")
@pytest.mark.parametrize(("tokens", "code"), [(99, 3), (261, 2)])
def test_macro_reports_short_empty_output_alongside_malformed_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tokens: int, code: int
) -> None:
    monkeypatch.setattr(policy_principles, "present_shapes", lambda *_: {Shape.FIELD_COPIES})
    fake = MacroFake([])

    def invoke(request: claude.Invocation) -> claude.InvocationResult:
        result = fake(request)
        if request.policy_role is PolicyRole.PRINCIPLE:
            return replace(result, structured_output={"findings": None})
        record = replace(result.record, output_tokens=tokens, output_tokens_reported=True)
        return replace(result, record=record)

    assert main(["run", "octo/synthetic@sample", "macro"], invoke=invoke) == code
    output = capsys.readouterr()
    assert "出力がスキーマに適合しません" in output.err
    assert ("警告: 出力が 99 トークン" in output.err) is (code == 3)
