"""偽の Codex CLI で群間の懸念の引き継ぎと検証役の失敗を確かめる。"""

import json
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from typing import Any

import pytest
from runs_support import write_runs_json
from test_usage import invocation

from jigane.commands import main
from jigane.git import process
from jigane.llm_runs.usage_table import TOTAL_ROLE
from jigane.reviews.resolution import resolve_review
from jigane.storage.store import data_home

REVIEW = "octo/synthetic@sample"


def _debug(group: str) -> int:
    return main(["debug", "run", REVIEW, group, "--yes", "--max-usd", "1", "--json"])


def test_codex_group_runs_carry_saved_macro_concern_to_micro(
    codex_trace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = codex_trace.with_name("codex")
    concern = "戻り値の契約が懸念である。"
    discarded = [{"source": "S1", "reason": "図から確かめられない。", "concern": concern}]
    binary.write_text(
        binary.read_text(encoding="utf-8").replace(
            "role, output = _group_response(schema, prompt)",
            "role, output = _group_response(schema, prompt)\n"
            "if role == 'diagram':\n"
            f"    output['discarded'] = {discarded!r}\n"
            "if role == 'discovery':\n"
            f"    assert {concern!r} in prompt",
        ),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert _debug("macro") == 0
    [nit] = json.loads(capsys.readouterr().out)["findings"]
    assert _debug("micro") == 0
    report = json.loads(capsys.readouterr().out)
    discovery = report["run"]["invocations"][0]
    assert discovery["concerns"]["passed"] == [{"id": nit["id"], "text": concern}]
    assert codex_trace.read_text(encoding="utf-8").splitlines() == [
        "diagram",
        "discovery",
        "rebuttal",
        "judge",
        "themes",
    ]
    assert not list(data_home().glob("codex-budget-*.json"))


@pytest.mark.parametrize("failure", ["missing", "timeout"])
def test_codex_verifier_failure_keeps_unverified_candidate_and_attempt_costs(
    codex_trace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    launch = process.run_codex_resolved

    def fake(args: list[str], **kwargs: Any) -> CompletedProcess[str]:  # noqa: ANN401 起動引数
        result = launch(args, **kwargs)
        if args[1] == "exec":
            schema = Path(args[args.index("--output-schema") + 1]).read_text(encoding="utf-8")
            if '"challenge"' in schema:
                if failure == "timeout":
                    raise TimeoutExpired(args, timeout=0)
                output = Path(args[args.index("--output-last-message") + 1])
                output.write_text('{"items":[]}', encoding="utf-8")
        return result

    monkeypatch.setattr(process, "run_codex_resolved", fake)
    capsys.readouterr()
    assert _debug("security") == 3
    report = json.loads(capsys.readouterr().out)
    assert report["run"]["unverified_candidates"] == 1
    assert codex_trace.read_text(encoding="utf-8").splitlines() == [
        "discovery",
        "rebuttal",
        "rebuttal",
    ]
    rows = report["run"]["invocations"]
    assert [row["cost_source"] for row in rows] == [
        "usage_priced",
        *["unreported" if failure == "timeout" else "usage_priced"] * 2,
    ]


def test_old_codex_usage_and_reservation_files_survive_new_group_run(
    codex_trace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = resolve_review(REVIEW).directory()
    model = "gpt-5.4-mini-2026-03-17"
    cost = 0.000029475
    row = invocation(
        "diagram",
        model=model,
        response_model=model,
        cost_source="usage_priced",
        cost_usd=cost,
        max_budget_usd=0.336864,
    )
    write_runs_json(
        directory, {"macro": {"shape": "diagram", "model": model, "invocations": [row]}}
    )
    ledger = data_home() / "codex-budget-past.json"
    old_ledger = '{"version":1,"budget_id":"past"}\n'
    ledger.write_text(old_ledger, encoding="utf-8")
    capsys.readouterr()

    assert main(["estimate", REVIEW, "macro", "--json"]) == 0
    previous = json.loads(capsys.readouterr().out)["past_run"]
    assert previous["model"] == model
    assert previous["cost_usd"] == pytest.approx(cost)
    assert not codex_trace.with_name("login-checked").exists()

    assert _debug("micro") == 0
    capsys.readouterr()
    assert main(["show", REVIEW, "macro", "--json"]) == 0
    previous_run = json.loads(capsys.readouterr().out)["run"]
    assert previous_run["model"] == model
    assert previous_run["cost_usd"] == pytest.approx(cost)
    stored_runs = json.loads((directory / "runs.json").read_text(encoding="utf-8"))
    assert stored_runs["macro"]["invocations"] == [row]
    assert main(["usage", REVIEW, "--json"]) == 0
    totals = {
        total["group"]: total
        for total in json.loads(capsys.readouterr().out)["rows"]
        if total["role"] == TOTAL_ROLE
    }
    assert totals["方針"]["usage_priced_cost_usd"] == pytest.approx(cost)
    assert totals["実装"]["usage_priced_cost_usd"] > 0
    assert ledger.read_text(encoding="utf-8") == old_ledger
