"""偽の Codex で micro と security の発見資料と検証経路を確かめる。"""

import json
from pathlib import Path

import pytest
from pr_support import commit_all

from jigane.commands import main
from jigane.git import process
from jigane.llm_runs import codex
from jigane.skills import adversarial
from jigane.storage.agent_selection import Agent, save_agent


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
@pytest.mark.parametrize("indivisible", [False, True])
def test_codex_discovery_divides_only_between_hunks(
    synthetic_diff: Path,
    group: str,
    *,
    indivisible: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = synthetic_diff / "repo"
    source = repo / "sample.py"
    separator = "".join(f"    # separator {n}\n" for n in range(12))
    if indivisible:
        separator = ""
    base = "def sample():\n    pass\n" + "    # untouched\n" * 6
    source.write_text(base + "    # before\n" + separator + "    # previous\n")
    commit_all(repo, "prepare the test file")
    first = "甲" * 5_000
    second = "乙" * 5_000
    source.write_text(base + f"    # {first}\n" + separator + f"    # {second}\n")
    commit_all(repo, "replace the two lines")
    save_agent(Agent.CODEX)

    def forbidden_call(*_args: object, **_kwargs: object) -> None:
        pytest.fail("discovery preflight must remain local")

    monkeypatch.setattr(process, "run_codex_resolved", forbidden_call)
    assert main(["new", "range", "HEAD~1..HEAD", "--name", "wide"]) == 0
    capsys.readouterr()
    assert main(["debug", "prompt", "wide", group, "--json"]) == (2 if indivisible else 0)
    captured = capsys.readouterr()
    if indivisible:
        assert "sample.py" in captured.err
        assert "@@" in captured.err
        assert main(["debug", "run", "wide", group, "--json", "--yes", "--max-usd", "1"]) == 2
        return

    prompts = [entry["text"] for entry in json.loads(captured.out)["prompts"]]
    assert len(prompts) == 2
    for prompt, payload in zip(prompts, (first, second), strict=True):
        assert len((codex.REVIEW_BOUNDARY + prompt).encode("utf-8")) <= 32_768
        assert prompt.count("@@ -") == 1
        assert "+++ b/sample.py" in prompt
        assert payload in prompt
        assert "1: def sample():" in prompt
    assert main(["estimate", "wide", group, "--json"]) == 0
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["planned_invocations"] == len(prompts)
    assert estimate["prompt_bytes"] == [
        len((codex.REVIEW_BOUNDARY + prompt).encode("utf-8")) for prompt in prompts
    ]
