"""偽の Codex で micro と security の発見資料と検証経路を確かめる。"""

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from fakes import write_fake_claude_wrapper
from pr_support import commit_all

from jigane.commands import main
from jigane.git import process
from jigane.groups.definitions import GroupId
from jigane.llm_runs import codex, pricing
from jigane.skills import adversarial
from jigane.storage.agent_selection import Agent, save_agent
from tests_posix.codex_cli_support import fake_chatgpt_login


@pytest.mark.parametrize("group", ["micro", "security"])
def test_codex_sharded_reviews_use_static_verification(
    codex_trace: Path,
    group: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def no_extra_reads(*_: object) -> None:
        raise AssertionError("Codex verification may use only preassembled materials")

    monkeypatch.setattr(adversarial, "_readable_lines", no_extra_reads)
    review = "octo/synthetic@sample"
    capsys.readouterr()
    assert main(["estimate", review, group, "--json"]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["agent"] == "codex"
    assert estimate["expected"]["total_usd"] > estimate["launches"][0]["input_usd"]
    model = codex.discovery_model(GroupId(group))
    assert (model.name, model.effort, model.expected_output_tokens) == (
        "gpt-6-luna",
        "high",
        16_384,
    )
    [launch] = estimate["launches"]
    assert (launch["role"], launch["model"], launch["effort"]) == ("discovery", model.name, "high")
    assert (estimate["model"], estimate["effort"]) == (model.name, "high")
    [prompt_bytes] = estimate["prompt_bytes"]
    input_tokens = pricing.estimate_input_tokens(
        prompt_bytes + len(codex.REVIEW_BOUNDARY.encode())
    )
    expected = estimate["expected"]
    assert expected["discovery_estimate_usd"] == (
        model.estimate_nanos(input_tokens, 16_384) / codex.NANOS_PER_USD
    )
    assert estimate["max_budget_usd_per_call"] is None
    assert estimate["planned_invocations"] == 1
    assert estimate["has_verifier"]
    assert estimate["noninteractive_run_available"]

    assert main(["debug", "run", review, group, "--yes", "--max-usd", "100", "--json"]) == 2
    stopped = json.loads(capsys.readouterr().out)
    assert stopped["stopped"] == "confirmation_required"
    assert stopped["estimated_usd"] is None
    if group == "security":
        assert main(["run", review, group, "--mcps", "synthetic", "--json"]) == 2
        err = capsys.readouterr().err
        assert "mcp-codex" in err
    assert not codex_trace.exists()

    assert (
        main(["run", review, group, "--json"], is_terminal=lambda: True, read_answer=lambda _: "y")
        == 0
    )
    captured = capsys.readouterr()
    assert "gpt-6-luna（effort high）" in captured.err
    assert f"群の見込み: {expected['total_usd']:.4f} USD" in captured.err
    body = json.loads(captured.out)
    assert body["findings"][0]["adjudication"]["verdict"] == "kept"
    assert body["totals"]["usage_priced_cost_usd"] > 0
    called = codex_trace.read_text(encoding="utf-8").splitlines()
    assert called == ["discovery", "rebuttal", "judge", *(["themes"] if group == "micro" else [])]


@pytest.mark.parametrize("group", ["micro", "security"])
@pytest.mark.parametrize("one_hunk", [False, True])
@pytest.mark.parametrize("first_length", [5_000, 12_000])
@pytest.mark.parametrize("shard_result", ["no_usage", "short_empty", "both_failed"])
def test_codex_discovery_uses_the_same_file_shards_as_claude(
    synthetic_diff: Path,
    group: str,
    *,
    one_hunk: bool,
    first_length: int,
    shard_result: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = synthetic_diff / "repo"
    source = repo / "sample.py"
    separator = "" if one_hunk else "".join(f"    # separator {n}\n" for n in range(12))
    base = "def sample():\n    pass\n" + "    # untouched\n" * 6
    source.write_text(base + "    # before\n" + separator + "    # previous\n")
    commit_all(repo, "prepare the test file")
    first = "甲" * first_length
    second = "乙" * 5_000
    source.write_text(base + f"    # {first}\n" + separator + f"    # {second}\n")
    (repo / "tiny.py").write_text("VALUE = 'separate change'\n", encoding="utf-8")
    commit_all(repo, "replace the two lines")
    fake_chatgpt_login(synthetic_diff, monkeypatch)
    requests: list[str] = []

    def fake_codex(args: list[str], **kwargs: object) -> CompletedProcess[str]:
        prompt = kwargs["input"]
        assert isinstance(prompt, str)
        requests.append(prompt)
        if args[1] == "login":
            return CompletedProcess(args, 0, "Logged in using ChatGPT", "")
        if _other_shard_failed(prompt, shard_result):
            return CompletedProcess(args, 7, "", "Synthetic review failure")
        output = Path(args[args.index("--output-last-message") + 1])
        output.write_text('{"findings": []}', encoding="utf-8")
        if shard_result == "no_usage":
            return CompletedProcess(args, 0, "", "")
        tokens = 99 if "+++ b/tiny.py" in prompt else 261
        event = {"type": "turn.completed", "usage": {"output_tokens": tokens}}
        return CompletedProcess(args, 0, json.dumps(event), "")

    monkeypatch.setattr(process, "run_codex_resolved", fake_codex)
    assert main(["new", "range", "HEAD~1..HEAD", "--name", "wide"]) == 0
    capsys.readouterr()

    def preview(agent: Agent) -> list[str]:
        save_agent(agent)
        assert main(["debug", "prompt", "wide", group, "--json"]) == 0
        return [entry["text"] for entry in json.loads(capsys.readouterr().out)["prompts"]]

    claude_prompts = preview(Agent.CLAUDE)
    prompts = preview(Agent.CODEX)
    assert prompts == claude_prompts
    [sample_prompt] = [prompt for prompt in prompts if "+++ b/sample.py" in prompt]
    assert sample_prompt.count("@@ -") == (1 if one_hunk else 2)
    for payload in (first, second):
        assert sample_prompt.count(payload) == 1
    assert "### 関連する宣言（変更後）" not in sample_prompt
    sizes = [len((codex.REVIEW_BOUNDARY + prompt).encode("utf-8")) for prompt in prompts]
    assert sum("+++ b/tiny.py" in prompt for prompt in prompts) == 1
    assert max(sizes) > 32_768
    assert main(["estimate", "wide", group, "--json"]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["planned_invocations"] == len(prompts)
    assert estimate["prompt_bytes"] == [len(prompt.encode("utf-8")) for prompt in prompts]
    assert estimate["noninteractive_run_available"] is True
    assert not requests
    _assert_shard_discovery_result(group, len(prompts), capsys, shard_result)
    assert sorted(filter(None, requests)) == sorted(codex.REVIEW_BOUNDARY + p for p in prompts)
    if shard_result != "no_usage":
        _assert_claude_shard_discovery_result(
            group, claude_prompts, synthetic_diff, monkeypatch, capsys, shard_result
        )


def _other_shard_failed(prompt: str, shard_result: str) -> bool:
    return shard_result == "both_failed" and "+++ b/tiny.py" not in prompt


def _assert_claude_shard_discovery_result(
    group: str,
    prompts: list[str],
    fixture_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    shard_result: str,
) -> None:
    run_resolved = process._run_resolved
    requests: list[str] = []

    def fake_claude(args: list[str], **kwargs: object) -> CompletedProcess[str]:
        if args[1:] == ["--version"]:
            return CompletedProcess(args, 0, "2.1.308 (Claude Code)", "")
        if "-p" not in args:
            return run_resolved(args, **kwargs)
        prompt = kwargs["input"]
        assert isinstance(prompt, str)
        requests.append(prompt)
        if _other_shard_failed(prompt, shard_result):
            return CompletedProcess(args, 7, "", "Synthetic review failure")
        tokens = 99 if "+++ b/tiny.py" in prompt else 261
        result = {"structured_output": {"findings": []}, "usage": {"output_tokens": tokens}}
        return CompletedProcess(args, 0, json.dumps(result), "")

    binary = write_fake_claude_wrapper(fixture_dir)
    monkeypatch.setenv(process.CLAUDE_BINARY_ENVIRONMENT, str(binary))
    save_agent(Agent.CLAUDE)
    monkeypatch.setattr(process, "_run_resolved", fake_claude)
    _assert_shard_discovery_result(group, len(prompts), capsys, shard_result)
    assert sorted(requests) == sorted(prompts)


def _assert_shard_discovery_result(
    group: str, shards: int, capsys: pytest.CaptureFixture[str], shard_result: str
) -> None:
    assert main(["run", "wide", group, "--json"]) == (0 if shard_result == "no_usage" else 3)
    output = capsys.readouterr()
    report = json.loads(output.out)
    if shard_result == "no_usage":
        return
    kinds = [failure["kind"] for failure in report["failures"]]
    expected = ["short_empty_output"]
    if shard_result == "both_failed":
        expected.append("other")
        assert "終了コードが 7" in output.err
    assert sorted(kinds) == sorted(expected)
    assert "警告: 出力が 99 トークン" in output.err
    assert report["run"]["succeeded_shards"] == shards - len(expected)
