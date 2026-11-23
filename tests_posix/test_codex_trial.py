"""合成差分を使い、通常の CLI と偽の Codex 実行ファイルをつなぐ。"""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fakes import MacroFake
from pr_support import commit_all

from jigane.commands import main
from jigane.design.review_documents import create_design_doc_review
from jigane.groups.definitions import MACRO, MICRO
from jigane.groups.policy_runs import PolicyRole
from jigane.groups.viewpoints import Viewpoint
from jigane.llm_runs import claude, codex
from jigane.reviews.catalog import load_review
from jigane.reviews.resolution import resolve_review
from jigane.skills import macro, macro_compact, macro_principles_run, policy_principles, sharded
from jigane.skills.assembly import AssemblyInput
from jigane.skills.launch_prompts import LaunchInput, group_launch
from jigane.skills.mcp import NO_MCP
from jigane.skills.policy_principle_shapes import Shape
from jigane.storage.agent_selection import Agent, save_agent
from tests_posix.codex_cli_support import FAKE_CODEX, fake_chatgpt_login


@pytest.mark.parametrize(
    ("response", "exit_code", "failure"),
    [
        (
            """Path(args[args.index("--output-last-message") + 1]).write_text(
                json.dumps({"findings": [{
                    "severity": "warning", "viewpoint": "M1",
                    "claim": "The sample return value changed",
                    "body": "Check that callers accept the new value.",
                    "evidence": [{"path": "sample.py", "start_line": 2, "end_line": 2}],
                    "declaration_ref": None, "judgment_region": None, "references": [],
                    "in_scope": True, "scope_reason": "Changed return value", "deepens": None
                }], "merged": [], "discarded": []})
            )
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 5
}}))""",
            0,
            None,
        ),
        ("sys.exit(7)", 2, "終了コードが 7"),
        (
            (
                'print(\'{"type":"turn.completed",'
                '"usage":{"input_tokens":12,"output_tokens":5}}\'); '
                "sys.exit(7)"
            ),
            2,
            "終了コードが 7",
        ),
        (
            'Path(args[args.index("--output-last-message") + 1]).write_text("not JSON")',
            2,
            "構造化出力",
        ),
    ],
)
def test_codex_trial(
    synthetic_diff: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    response: str,
    exit_code: int,
    failure: str | None,
) -> None:
    binary = synthetic_diff / "codex"
    marker = synthetic_diff / "calls"
    binary.write_text(
        f"#!{sys.executable}\nfrom __future__ import annotations\n"
        + FAKE_CODEX.replace("{marker!r}", repr(str(marker))).replace("{response}", response),
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", f"{synthetic_diff}{os.pathsep}{os.environ['PATH']}")
    fake_chatgpt_login(synthetic_diff, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic")
    save_agent(Agent.CODEX)
    args = ["run", "octo/synthetic@sample", "macro", "--json"]
    capsys.readouterr()
    assert main(args, is_terminal=lambda: False) == 2
    assert not marker.exists()
    assert main(args, is_terminal=lambda: True, read_answer=lambda _: "n") == 2
    assert not marker.exists()
    capsys.readouterr()
    assert main(args, is_terminal=lambda: True, read_answer=lambda _: "y") == exit_code
    assert marker.read_text(encoding="utf-8") == "called\n"
    captured = capsys.readouterr()
    assert all(
        text in captured.err
        for text in (
            "保存された ChatGPT ログイン",
            "chatgpt.com",
            "gpt-6-luna",
            codex.NO_SPENDING_LIMIT,
        )
    )
    result = json.loads(captured.out)
    if failure is None:
        assert result["totals"]["cost_usd"] == pytest.approx(0.00000343)
        assert result["totals"]["input_tokens"] == 12
        assert result["findings"][0]["claim"] == "The sample return value changed"
        invocation = result["run"]["invocations"][0]
        assert invocation["input_tokens"] == 9
        assert invocation["cache_read_input_tokens"] == 3
        assert invocation["output_tokens"] == 5
        assert invocation["cost_source"] == "usage_priced"
        assert invocation["duration_ms"] > 0
        assert result["run"]["elapsed_s"] >= 0
        assert "max_budget_usd" not in invocation
        assert main(["object", args[1], "F1", "--reason", "Synthetic decision"]) == 0
        capsys.readouterr()
        assert main(["show", args[1], "--json"]) == 0
        assert "Synthetic decision" in capsys.readouterr().out
    else:
        assert failure in captured.err
        assert result["error"]["kind"] == "discovery"
    assert main(["usage", args[1], "--json"]) == 0
    usage = json.loads(capsys.readouterr().out)
    total = usage["rows"][-1]
    if failure is None:
        assert total["usage_priced_cost_usd"] == pytest.approx(0.00000343)
        _assert_failed_rerun_preserves_decision(binary, args, capsys)
    else:
        assert total["cost_usd"] is None
        assert total["unknown_cost_invocation_count"] == 1


@pytest.mark.parametrize("status", ["Not logged in", "API key configured - synthetic-mask"])
def test_codex_login_requires_chatgpt_before_launch(
    codex_trace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    login_mode = codex_trace.with_name("codex-login") / "login-mode"
    login_mode.write_text(status, encoding="utf-8")
    capsys.readouterr()
    assert main(["run", "octo/synthetic@sample", "macro", "--json"]) == 2
    captured = capsys.readouterr()
    assert "`codex login`" in captured.err
    assert status not in captured.err
    assert codex_trace.with_name("login-checked").exists()
    assert not codex_trace.exists()


@pytest.mark.parametrize("config_file", [".env", "AGENTS.md", "AGENTS.override.md"])
def test_codex_login_refuses_implicit_settings_without_launching(
    codex_trace: Path, capsys: pytest.CaptureFixture[str], config_file: str
) -> None:
    login_home = codex_trace.with_name("codex-login")
    (login_home / config_file).symlink_to(login_home / "login-mode")
    capsys.readouterr()
    assert main(["run", "octo/synthetic@sample", "macro", "--json"]) == 2
    assert f"Codex ホームの `{config_file}`" in capsys.readouterr().err
    assert not codex_trace.exists()


@pytest.mark.parametrize("key_initially_set", [False, True])
def test_codex_login_route_and_model_stay_bound_to_consent(
    codex_trace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    key_initially_set: bool,
) -> None:
    if key_initially_set:
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("CODEX_API_KEY", "synthetic")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "synthetic")
    monkeypatch.setenv("HTTPS_PROXY", "https://invalid.example")
    capsys.readouterr()
    args = ["octo/synthetic@sample", "macro"]
    assert main(["estimate", *args]) == 0
    estimate = capsys.readouterr().out
    assert "保存された ChatGPT ログイン" in estimate
    assert "gpt-6-luna（effort low" in estimate
    assert "群の見込み" in estimate
    assert not codex_trace.exists()
    assert main(["run", *args], is_terminal=lambda: True, read_answer=lambda _: "n") == 2
    assert not codex_trace.exists()
    capsys.readouterr()

    def accept(_: str) -> str:
        if key_initially_set:
            monkeypatch.delenv("OPENAI_API_KEY")
        else:
            monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
        return "y"

    assert main(["run", *args, "--json"], is_terminal=lambda: True, read_answer=accept) == 0
    captured = capsys.readouterr()
    assert "保存された ChatGPT ログイン" in captured.err
    assert "gpt-6-luna（effort low）" in captured.err
    assert "OPENAI_API_KEY" not in captured.err
    report = json.loads(captured.out)
    invocation = report["run"]["invocations"][0]
    assert invocation["model"] == invocation["response_model"] == "gpt-6-luna"
    assert invocation["cost_usd"] == pytest.approx(0.00000343)


def _assert_failed_rerun_preserves_decision(
    binary: Path, args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    binary.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1:3] == ['login', 'status']:\n"
        "    print('Logged in using ChatGPT')\n"
        "else:\n"
        "    sys.exit(7)\n",
        encoding="utf-8",
    )
    assert main(args, is_terminal=lambda: True, read_answer=lambda _: "y") == 2
    capsys.readouterr()
    assert main(["show", args[1], "macro", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["run"]["cost_usd"] is None
    assert shown["run"]["known_cost_usd"] == pytest.approx(0.00000343)
    assert shown["run"]["latest_attempt_elapsed_s"] >= 0
    assert "Synthetic decision" in json.dumps(shown)


@pytest.mark.parametrize(
    ("shape", "viewpoint", "expected"),
    [
        (Shape.FIELD_COPIES, "P1", ("gpt-6-luna", 100, 3_430)),
        (Shape.CALLERS, "P2", ("gpt-6-luna", 100, 3_430)),
        (Shape.DISCRIMINATORS, "P3", ("gpt-6.1-terra", 1_000, 34_150)),
        (Shape.SEQUENCE, "P4", ("gpt-6.2-sol", 2_000, 68_300)),
    ],
)
@pytest.mark.parametrize("execution", ["run", "debug"])
def test_codex_macro_runs_a_principle_separately(
    codex_trace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    shape: Shape,
    viewpoint: str,
    expected: tuple[str, int, int],
    execution: str,
) -> None:
    model, input_nanos, cost_nanos = expected
    monkeypatch.setattr(policy_principles, "present_shapes", lambda *_: {shape})
    args = ["octo/synthetic@sample", "macro", "--json"]
    capsys.readouterr()
    assert main(["estimate", *args]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["planned_invocations"] == 2
    assert estimate["cli_auth"] == "chatgpt_login"
    assert estimate["model"] == estimate["launches"][0]["model"] == "gpt-6-luna"
    assert estimate["effort"] == estimate["launches"][0]["effort"] == "low"
    assert estimate["noninteractive_requires"] == "codex login"
    assert (
        estimate["noninteractive_model"]
        == estimate["launches"][0]["noninteractive_model"]
        == codex.LOGIN_MODEL.name
    )
    launch = estimate["launches"][1]
    assert (launch["principle"], launch["model"], launch["effort"]) == (viewpoint, model, "high")
    assert (launch["noninteractive_model"], launch["noninteractive_effort"]) == (model, "high")
    assert launch["input_usd"] == pytest.approx(launch["input_tokens"] * input_nanos / 10**9)
    principle_model = codex.model_for(PolicyRole.PRINCIPLE, viewpoint)
    discovery, principle = (row["input_tokens"] for row in estimate["launches"])
    assert estimate["expected"]["total_usd"] == pytest.approx(
        (
            codex.LOGIN_MODEL.estimate_nanos(discovery, 8_192)
            + principle_model.estimate_nanos(principle, 8_192)
        )
        / 10**9
    )
    assert main(["debug", "prompt", *args]) == 0
    prompts = json.loads(capsys.readouterr().out)["prompts"]
    boundary_size = len(codex.REVIEW_BOUNDARY.encode("utf-8"))
    assert [p["bytes"] + boundary_size for p in prompts] == estimate["prompt_bytes"]
    assert all("### 変更節点と範囲" in p["text"] for p in prompts)
    assert all("classDiagram" not in p["text"] for p in prompts)
    assert all('1 "sample.py"' in p["text"] for p in prompts)
    expected_prompts = {
        role: codex.REVIEW_BOUNDARY + item["text"]
        for role, item in zip(("diagram", "principle"), prompts, strict=True)
    }
    codex_trace.with_name("expected-prompts.json").write_text(
        json.dumps(expected_prompts), encoding="utf-8"
    )
    assert not codex_trace.exists()
    command = (
        ["run", *args]
        if execution == "run"
        else ["debug", "run", *args, "--yes", "--max-usd", str(estimate["expected"]["total_usd"])]
    )
    assert main(command) == 0
    body = json.loads(capsys.readouterr().out)
    assert sorted(codex_trace.read_text(encoding="utf-8").splitlines()) == ["diagram", "principle"]
    assert [
        row["viewpoint"] for row in body["run"]["invocations"] if row["role"] == "principle"
    ] == [viewpoint]
    principle = next(row for row in body["run"]["invocations"] if row["role"] == "principle")
    assert principle["model"] == principle["response_model"] == model
    assert principle["cost_usd"] == pytest.approx(cost_nanos / 10**9)


def test_codex_cli_rejects_a_reported_model_mismatch(
    codex_trace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codex_trace.with_name("response-model").write_text("unexpected-model", encoding="utf-8")
    capsys.readouterr()
    assert main(["run", "octo/synthetic@sample", "macro", "--json"]) == 2
    captured = capsys.readouterr()
    assert "Codex の応答モデルが要求したモデルと一致しません" in captured.err
    assert main(["usage", "octo/synthetic@sample", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][-1]["cost_usd"] is None


def test_codex_macro_keeps_type_contract_and_caller_evidence(
    codex_trace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = codex_trace.parent / "repo"
    (repo / "sample.py").write_text(
        "class Base:\n    pass\n\n"
        "class Choice(Base):\n    value: str\n\n"
        "def sample(value: Choice) -> str:\n    return value.value\n",
        encoding="utf-8",
    )
    (repo / "consumer.py").write_text(
        "from sample import Choice, sample\n\n"
        "def consume(value: Choice) -> str:\n    return sample(value)\n",
        encoding="utf-8",
    )
    commit_all(repo, "Add a typed contract and its caller")
    assert main(["new", "range", "HEAD~1..HEAD", "--name", "structure"]) == 0
    capsys.readouterr()
    review_id = "octo/synthetic@structure"
    assert main(["debug", "prompt", review_id, "macro", "--json"]) == 0
    prompts = json.loads(capsys.readouterr().out)["prompts"]
    diagram = prompts[0]["text"]
    assert '2 "sample.py"' in diagram
    assert "#2:4 + class Choice : Base" in diagram
    assert "+5 value: str" in diagram
    assert any("def sample(value: Choice) -> str:" in prompt["text"] for prompt in prompts)
    assert '1 "consumer.py"' in diagram
    assert "4 Choice → #1 の consume (2)" in diagram
    assert "#2 → #1: 1" in diagram
    assert any("sample.value → #1 の consume (1)" in prompt["text"] for prompt in prompts[1:])
    assembly = AssemblyInput.load(load_review(resolve_review(review_id)))
    for region in assembly.review.map_kind.table.regions:
        assert region.id is not None
        assert region.new_span is not None
        assert f"{region.new_span.start_line}-{region.new_span.end_line}" in diagram
        assert region.qualified_name in diagram
    assert not codex_trace.exists()


@pytest.mark.usefixtures("synthetic_diff")
def test_claude_macro_debug_prompt_keeps_existing_materials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    save_agent(Agent.CLAUDE)
    review_id = "octo/synthetic@sample"
    assembly = AssemblyInput.load(load_review(resolve_review(review_id)))
    expected = [
        macro.remembered_prompt(assembly),
        *(item.prompt for item in policy_principles.principle_prompts(assembly)),
    ]
    capsys.readouterr()
    assert main(["debug", "prompt", review_id, "macro", "--json"]) == 0
    prompts = json.loads(capsys.readouterr().out)["prompts"]
    assert [item["text"] for item in prompts] == expected


@pytest.mark.usefixtures("synthetic_diff")
def test_compact_materials_are_selected_independently_of_the_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(policy_principles, "present_shapes", lambda *_: {Shape.FIELD_COPIES})
    assembly = AssemblyInput.load(load_review(resolve_review("octo/synthetic@sample")))
    budget = codex.review_budget(claude.RunBudget("m", 1.0, reserve_margin=False))
    full = replace(budget, compact_materials=False)

    compact_launch = group_launch(LaunchInput(assembly, MACRO, budget=budget))
    compact_prompts = compact_launch.prompts()
    assert [prompt.principle for prompt in compact_prompts] == [None, "P1"]
    assert all("### 変更節点と範囲" in prompt.text for prompt in compact_prompts)
    assert compact_launch.estimate().to_record()["materials_bytes"] == sum(
        prompt.size for prompt in compact_prompts
    )
    invoke = MacroFake([])
    macro_principles_run.run_policy(
        assembly,
        claude.RunSettings(budget, None, invoke, has_spending_limit=False, allow_tools=False),
    )
    assert {call.principle: call.prompt for call in invoke.calls} == {
        prompt.principle: prompt.text for prompt in compact_prompts
    }
    full_prompts = group_launch(LaunchInput(assembly, MACRO, budget=full)).prompts()
    assert full_prompts[0].text == macro.remembered_prompt(assembly)
    assert full_prompts[0].text != compact_prompts[0].text

    compact_shards = sharded.plan_shards(assembly, MICRO, NO_MCP, budget)
    assert compact_shards.hunks["sample.py"]
    full_shards = sharded.plan_shards(assembly, MICRO, NO_MCP, full)
    assert not full_shards.hunks
    assert full_shards.prompts != compact_shards.prompts


def test_large_codex_macro_principle_reaches_cli(
    codex_trace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(policy_principles, "present_shapes", lambda *_: {Shape.FIELD_COPIES})
    build = macro_compact.build_prompt

    def expanded(assembly: AssemblyInput, principle: Viewpoint | None = None) -> str:
        text = build(assembly, principle)
        return text + ("契" * 12_000 if principle else "")

    monkeypatch.setattr(macro_compact, "build_prompt", expanded)
    args = ["octo/synthetic@sample", "macro", "--json"]
    capsys.readouterr()
    assert main(["estimate", *args]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["prompt_bytes"][0] < 32_768 < estimate["prompt_bytes"][1]
    assert estimate["noninteractive_run_available"] is True
    assert main(["debug", "prompt", *args]) == 0
    prompts = json.loads(capsys.readouterr().out)["prompts"]
    expected_prompts = {
        role: codex.REVIEW_BOUNDARY + item["text"]
        for role, item in zip(("diagram", "principle"), prompts, strict=True)
    }
    codex_trace.with_name("expected-prompts.json").write_text(
        json.dumps(expected_prompts), encoding="utf-8"
    )
    assert main(["debug", "run", *args, "--yes", "--max-usd", "0.001"]) == 2
    assert json.loads(capsys.readouterr().out)["stopped"] == "estimate_exceeds_max_usd"
    assert not codex_trace.exists()
    expected = str(estimate["expected"]["total_usd"])
    assert main(["debug", "run", *args, "--yes", "--max-usd", expected]) == 0
    assert sorted(codex_trace.read_text(encoding="utf-8").splitlines()) == ["diagram", "principle"]


@pytest.mark.parametrize(
    ("group", "role"),
    [("macro", "diagram"), ("micro", "discovery"), ("security", "discovery")],
)
def test_codex_large_design_document_estimates_and_reaches_cli(
    codex_trace: Path, capsys: pytest.CaptureFixture[str], group: str, role: str
) -> None:
    repo = codex_trace.parent / "repo"
    document = (
        "# Design for sample.py\n\n" + "既存の実装を照合する。\n" * 1_600 + "\nEnd of design.\n"
    )
    assert 54_000 <= len(document.encode("utf-8")) <= 56_000
    (repo / "design.md").write_text(document, encoding="utf-8")
    review = create_design_doc_review(repo, "design.md", select=[])
    args = [str(review.id), group, "--json"]
    capsys.readouterr()

    assert main(["estimate", *args]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["agent"] == "codex"
    assert estimate["noninteractive_run_available"] is True
    assert estimate["expected"]["total_usd"] > 0
    assert "max_prompt_bytes" not in estimate
    assert "exceeds_prompt_limit" not in estimate
    if group == "macro":
        note = estimate["note"]
        assert "設計書の全文と構造" in note
        assert "境界文を含む初期プロンプト" in note
        assert "出力と推論は計 8,192 トークン" in note
        assert "公開 API 単価で換算した見込みを expected.total_usd に出します" in note
        assert "実行後の換算額は Codex CLI が報告した使用量から求めます" in note
        assert "過去の実額の倍率" not in note
    assert main(["debug", "prompt", *args]) == 0
    [prompt] = json.loads(capsys.readouterr().out)["prompts"]
    assert "End of design." in prompt["text"]
    assert prompt["text"].count("既存の実装を照合する。") == 1_600
    sent = codex.REVIEW_BOUNDARY + prompt["text"]
    assert estimate["prompt_bytes"] == [len(sent.encode("utf-8"))]
    assert estimate["prompt_bytes"][0] > 32_768
    assert main(["estimate", *args[:-1]]) == 0
    output = capsys.readouterr().out
    assert f"Codex の初期起動 1 回（各プロンプト {estimate['prompt_bytes'][0]} バイト）" in output
    assert "を超える初期プロンプト" not in output
    assert not codex_trace.with_name("login-checked").exists()
    assert not codex_trace.exists()

    codex_trace.with_name("expected-prompts.json").write_text(
        json.dumps({role: sent}), encoding="utf-8"
    )
    assert main(["run", *args]) == 0
    assert codex_trace.read_text(encoding="utf-8").splitlines() == [role]
