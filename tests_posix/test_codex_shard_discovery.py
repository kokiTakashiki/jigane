"""偽の Codex で micro と security の発見資料と検証経路を確かめる。"""

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pr_support import commit_all

from jigane.commands import main
from jigane.git import process
from jigane.llm_runs import codex
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

    monkeypatch.setattr(adversarial, "_checked_excerpts", no_extra_reads)
    review = "octo/synthetic@sample"
    capsys.readouterr()
    assert main(["estimate", review, group, "--json"]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["agent"] == "codex"
    assert estimate["expected"]["total_usd"] > estimate["launches"][0]["input_usd"]
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

    assert main(["run", review, group, "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["findings"][0]["adjudication"]["verdict"] == "kept"
    assert body["totals"]["usage_priced_cost_usd"] > 0
    called = codex_trace.read_text(encoding="utf-8").splitlines()
    assert called == ["discovery", "rebuttal", "judge", *(["themes"] if group == "micro" else [])]


@pytest.mark.parametrize("group", ["micro", "security"])
@pytest.mark.parametrize(
    ("one_hunk", "first_length", "large"),
    [(False, 5_000, False), (False, 12_000, True), (True, 12_000, True)],
)
def test_codex_discovery_divides_only_between_hunks(
    synthetic_diff: Path,
    group: str,
    *,
    one_hunk: bool,
    first_length: int,
    large: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = synthetic_diff / "repo"
    source = repo / "sample.py"
    separator = "".join(f"    # separator {n}\n" for n in range(12))
    if one_hunk:
        separator = ""
    base = "def sample():\n    pass\n" + "    # untouched\n" * 6
    source.write_text(base + "    # before\n" + separator + "    # previous\n")
    commit_all(repo, "prepare the test file")
    first = "甲" * first_length
    second = "乙" * 5_000
    source.write_text(base + f"    # {first}\n" + separator + f"    # {second}\n")
    (repo / "tiny.py").write_text("VALUE = 'separate change'\n", encoding="utf-8")
    commit_all(repo, "replace the two lines")
    save_agent(Agent.CODEX)
    fake_chatgpt_login(synthetic_diff, monkeypatch)
    requests: list[str] = []

    def fake_codex(args: list[str], **kwargs: object) -> CompletedProcess[str]:
        prompt = kwargs["input"]
        assert isinstance(prompt, str)
        requests.append(prompt)
        if args[1] == "login":
            return CompletedProcess(args, 0, "Logged in using ChatGPT", "")
        output = Path(args[args.index("--output-last-message") + 1])
        output.write_text('{"findings": []}', encoding="utf-8")
        return CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(process, "run_codex_resolved", fake_codex)
    assert main(["new", "range", "HEAD~1..HEAD", "--name", "wide"]) == 0
    capsys.readouterr()
    assert main(["debug", "prompt", "wide", group, "--json"]) == 0
    prompts = [entry["text"] for entry in json.loads(capsys.readouterr().out)["prompts"]]
    sample_prompts = [prompt for prompt in prompts if "+++ b/sample.py" in prompt]
    assert len(sample_prompts) == (1 if one_hunk else 2)
    if not one_hunk:
        assert all(
            payload in prompt
            for payload, prompt in zip((first, second), sample_prompts, strict=True)
        )
    for payload in (first, second):
        assert sum(prompt.count(payload) for prompt in sample_prompts) == 1
    sizes = [len((codex.REVIEW_BOUNDARY + prompt).encode("utf-8")) for prompt in prompts]
    for prompt in sample_prompts:
        assert "1: def sample():" in prompt
    for prompt, size in zip(prompts, sizes, strict=True):
        if size > 32_768:
            assert "+++ b/tiny.py" not in prompt
            assert prompt.count("@@ -") == 1
    assert sum("+++ b/tiny.py" in prompt for prompt in prompts) == 1
    assert (max(sizes) > 32_768) == large
    assert main(["estimate", "wide", group, "--json"]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["planned_invocations"] == len(prompts)
    assert estimate["prompt_bytes"] == sizes
    assert estimate["noninteractive_run_available"] is True
    assert not requests
    assert main(["run", "wide", group, "--json"]) == 0
    assert sorted(filter(None, requests)) == sorted(codex.REVIEW_BOUNDARY + p for p in prompts)
