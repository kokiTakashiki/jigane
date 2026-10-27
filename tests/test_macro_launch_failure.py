import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFake, as_run, discovered, settings_for

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings.records import load_findings_state, triaged_dicts
from jigane.groups.definitions import GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.cost import (
    BudgetOutcome,
    Cost,
    LimitSubstitute,
    Measured,
    NotCharged,
    Unreported,
    cost_as_record,
    cost_from_record,
    cost_text,
    total_by_source,
)
from jigane.llm_runs.launch_failure import (
    DiscoveryLaunchFailed,
    Disposition,
    FailedInvocation,
    FailureRound,
    FailureView,
    RunRecordKind,
    Stage,
    StoredFailure,
    earlier_failures,
    failure_from_record,
    failure_views,
)
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro, pipeline
from jigane.skills.assembly import AssemblyInput
from jigane.skills.group_result import Failed, HasFindings, group_outcome
from jigane.skills.pipeline import ShardedGroupRun

BUDGET = claude.FailureKind.BUDGET_EXCEEDED


def read_failures(state: dict[str, Any]) -> list[StoredFailure]:
    return list(ShardedGroupRun.from_json(state).launch_failures or ())


def views_of(state: dict[str, Any]) -> tuple[FailureView, ...]:
    return failure_views(read_failures(state))


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _budget_error(cost: float | None) -> claude.ClaudeInvocationError:
    payload = {} if cost is None else {"total_cost_usd": cost}
    record = claude.failure_record(payload, 0.5)
    return claude.ClaudeInvocationError(
        "claude が is_error を返しました: error_max_budget_usd",
        record,
        BUDGET,
    )


def _fail(review: Review, error: claude.ClaudeInvocationError) -> None:
    def invoke(_invocation: claude.Invocation) -> claude.InvocationResult:
        raise error

    with pytest.raises(macro.LaunchFailed):
        macro.run_macro(AssemblyInput.load(review), settings_for(invoke, max_budget_usd=0.5))


def _runs(review: Review) -> dict[str, Any]:
    runs: dict[str, Any] = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))
    return runs


def test_budget_failure_keeps_the_reported_cost_and_the_failure(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    state = _runs(review)["macro"]
    (record,) = state["launch_failures"]
    failure = failure_from_record(record)
    assert failure.stage is Stage.DISCOVERY
    assert failure.kind is BUDGET
    assert failure.disposition is Disposition.NOT_RERUN
    assert state["invocations"] == []
    assert record["cost_usd"] == 0.42
    assert record["cost_source"] == "measured"
    (invocation,) = [item.to_json() for item in pipeline.all_invocations(as_run(state))]
    assert invocation["cost_usd"] == 0.42
    assert state["ran_at"]
    assert pipeline.summarize_invocations(as_run(state))["cost_usd"] == 0.42


def test_budget_failure_without_a_reported_cost_records_the_limit_as_the_limit(
    project: Path,
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(None))
    (record,) = _runs(review)["macro"]["launch_failures"]
    assert record["cost_usd"] == 0.5
    assert record["cost_source"] == "limit_substitute"
    assert failure_from_record(record).cost == LimitSubstitute(0.5)


def test_failed_run_keeps_the_previous_findings(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    findings_path = review.id.directory() / "findings.json"
    before = findings_path.read_text("utf-8")
    _fail(review, _budget_error(0.3))
    assert findings_path.read_text("utf-8") == before


def test_show_and_usage_report_the_budget_failure(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    capsys.readouterr()
    assert main(["usage", "shop:p", "--json"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)["rows"]
    total = next(row for row in rows if row["role"] == "合計")
    assert total["cost_usd"] == 0.42
    outcome = group_outcome(as_run(_runs(review)["macro"]), 0)
    assert isinstance(outcome, Failed)
    assert "上限で失敗" in outcome.reason
    assert "0.4200 USD（使った額）" in outcome.reason


@pytest.mark.parametrize(
    ("cost", "shown_cost"),
    [(0.42, "0.4200 USD（使った額）"), (None, "推定。上限の額で代用し、実際に使った額は不明")],
)
def test_show_macro_reports_a_first_run_failure_without_any_saved_findings(
    project: Path, capsys: pytest.CaptureFixture[str], cost: float | None, shown_cost: str
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(cost))
    assert macro.GROUP not in load_findings_state(review.id.directory()).groups
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "未実行" not in out
    assert "上限で失敗" in out
    assert shown_cost in out
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["findings"] == []
    assert body["result"] == "failed"
    assert body["run"] is not None
    (line,) = (view["text"] for view in body["run"]["failures"])
    assert "上限で失敗" in line
    assert shown_cost in line


def test_limit_cost_is_called_the_limit_in_the_failure_line() -> None:
    failure = DiscoveryLaunchFailed(
        0,
        "diagram",
        BUDGET,
        "r",
        1,
        Disposition.NOT_RERUN,
        ran_at="2026-10-21T00:00:00+00:00",
        invocation=FailedInvocation(
            replace(claude.parse_record({}, 0.5), cost=LimitSubstitute(0.5)), "v", 10
        ),
    )
    (line,) = (v.text for v in failure_views([failure]))
    assert "上限で失敗" in line
    assert "0.5000 USD（推定。上限の額で代用し、実際に使った額は不明）" in line
    assert "時刻 2026-10-21" in line


def _completed(payload: dict[str, Any], returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, json.dumps(payload), "")


def test_claude_run_attaches_the_cost_when_the_budget_failure_exits_nonzero() -> None:
    payload = {"subtype": "error_max_budget_usd", "total_cost_usd": 0.31, "is_error": True}
    invocation = claude.Invocation("p", {}, Path(), claude.LaunchBudget(0.35, 0.3), "m")
    with pytest.raises(claude.ClaudeInvocationError) as caught:
        claude.run(invocation, runner=lambda *_a, **_k: _completed(payload, 1))
    assert caught.value.kind is BUDGET
    assert caught.value.cost == Measured(0.31)
    assert caught.value.record.cost == Measured(0.31)


def test_claude_run_keeps_the_measured_cost_when_structured_output_is_missing() -> None:
    payload = {"subtype": "success", "total_cost_usd": 0.12, "is_error": False}
    invocation = claude.Invocation("p", {}, Path(), claude.LaunchBudget(0.35, 0.3), "m")
    with pytest.raises(claude.ClaudeInvocationError) as caught:
        claude.run(invocation, runner=lambda *_a, **_k: _completed(payload, 0))
    assert caught.value.kind is claude.FailureKind.BAD_OUTPUT
    assert caught.value.cost == Measured(0.12)
    assert caught.value.record.cost == Measured(0.12)


def test_claude_run_marks_a_missing_cost_as_not_reported() -> None:
    payload = {"subtype": "error_max_budget_usd", "is_error": True}
    invocation = claude.Invocation("p", {}, Path(), claude.LaunchBudget(0.35, 0.3), "m")
    with pytest.raises(claude.ClaudeInvocationError) as caught:
        claude.run(invocation, runner=lambda *_a, **_k: _completed(payload, 0))
    assert caught.value.cost == LimitSubstitute(0.35)


@pytest.mark.parametrize("cost", [Measured(1.5), LimitSubstitute(0.5), NotCharged(), Unreported()])
def test_every_cost_value_round_trips_through_its_record(cost: Cost) -> None:
    assert cost_from_record(cost_as_record(cost)) == cost
    failure = DiscoveryLaunchFailed(
        0,
        "diagram",
        BUDGET,
        "r",
        1,
        Disposition.NOT_RERUN,
        ran_at="t",
        invocation=FailedInvocation(replace(claude.parse_record({}, 0.5), cost=cost), "v", 10),
    )
    stored = json.loads(json.dumps(failure.as_record()))
    assert failure_from_record(stored).as_record() == failure.as_record()


def test_measured_source_is_read_as_measured() -> None:
    assert cost_from_record({"cost_usd": 1.0, "cost_source": "measured"}) == Measured(1.0)


def test_recovered_failures_are_not_reported() -> None:
    recovered = DiscoveryLaunchFailed(0, "diagram", BUDGET, "r", 1, Disposition.RECOVERED)
    assert failure_views([recovered]) == ()


def test_total_by_source_counts_the_limit_substitutes_apart() -> None:
    items: list[dict[str, Any]] = [
        {"cost_usd": 0.2, "cost_source": "measured"},
        {"cost_usd": 0.5, "cost_source": "limit_substitute"},
        {"cost_usd": 0.0, "cost_source": "unreported"},
        {"cost_usd": 0.3, "cost_source": "measured"},
    ]
    totals = total_by_source(map(cost_from_record, items))
    assert totals.limit_substitute == 0.5
    assert totals.measured == pytest.approx(0.5)


def test_usage_separates_the_substituted_cost(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    _fail(review, _budget_error(None))
    capsys.readouterr()
    assert main(["usage", "shop:p", "--json"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)["rows"]
    total = next(row for row in rows if row["role"] == "合計")
    # 前の回の額と失敗の額（上限の額で代用した 0.5）の両方を数える
    assert total["cost_usd"] == pytest.approx(0.6)
    assert total["substituted_cost_usd"] == 0.5
    assert main(["usage", "shop:p"]) == EXIT_OK
    assert "うち推定（上限の額で代用） 0.5000 USD" in capsys.readouterr().out
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert shown["run"]["substituted_cost_usd"] == 0.5
    (line,) = (view["text"] for view in shown["run"]["failures"])
    assert "上限で失敗" in line
    assert "推定。上限の額で代用" in line


def test_failure_after_a_run_keeps_the_previous_run_record_and_adds_the_failure(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    before = _runs(review)["macro"]
    _fail(review, _budget_error(0.42))
    after = _runs(review)["macro"]
    assert after["ran_at"] == before["ran_at"]
    assert after["discarded"] == before["discarded"]
    assert after["invocations"] == before["invocations"]
    (record,) = after["launch_failures"]
    assert failure_from_record(record).cost == Measured(0.42)
    # 二度目の失敗は、前の失敗の記録に積まれる
    _fail(review, _budget_error(0.1))
    again = _runs(review)["macro"]
    assert again["invocations"] == before["invocations"]
    stacked = [failure_from_record(r).cost for r in again["launch_failures"]]
    assert stacked == [Measured(0.42), Measured(0.1)]
    capsys.readouterr()
    assert main(["usage", "shop:p", "--json"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)["rows"]
    total = next(row for row in rows if row["role"] == "合計")
    previous_cost = sum(row["cost_usd"] for row in before["invocations"])
    assert total["cost_usd"] == pytest.approx(previous_cost + 0.52)


def test_show_prints_previous_findings_and_the_latest_failure(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    macro.run_macro(
        AssemblyInput.load(review), settings_for(MacroFake([discovered()]), max_budget_usd=0.5)
    )
    findings = triaged_dicts(load_findings_state(review.id.directory()).groups[macro.GROUP])
    assert findings
    ran_at = _runs(review)["macro"]["ran_at"]
    _fail(review, _budget_error(0.42))
    assert _runs(review)["macro"]["ran_at"] == ran_at
    assert isinstance(group_outcome(as_run(_runs(review)["macro"]), len(findings)), HasFindings)
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "上限で失敗" in out
    assert "0.4200 USD（使った額）" in out
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["result"] == "findings"
    assert body["findings"]
    (line,) = (view["text"] for view in body["run"]["failures"])
    assert "上限で失敗" in line
    assert body["run"]["ran_at"] == ran_at


def test_record_kind_is_stored_explicitly(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.1))
    assert RunRecordKind.of_record(_runs(review)["macro"]) is RunRecordKind.FAILED_ONLY
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    assert RunRecordKind.of_record(_runs(review)["macro"]) is RunRecordKind.SUCCEEDED
    _fail(review, _budget_error(0.1))
    assert RunRecordKind.of_record(_runs(review)["macro"]) is RunRecordKind.SUCCEEDED_THEN_FAILED


def test_records_without_a_kind_are_read_as_succeeded() -> None:
    runs = pipeline.load_runs({"macro": {"discovered": 1}}).groups
    macro_run = runs[GroupId.MACRO]
    assert isinstance(macro_run, pipeline.PolicyGroupRun)
    assert RunRecordKind.of_record(macro_run.to_json()) is RunRecordKind.SUCCEEDED


def test_failure_row_is_marked_over_budget_by_its_kind(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    state = _runs(review)["macro"]
    (record,) = state["launch_failures"]
    assert record["invocation"]["max_budget_usd"] == 0.5
    (row,) = [item.to_json() for item in pipeline.all_invocations(as_run(state))]
    assert row["budget_outcome"] == "stopped_at_limit"
    assert "over_budget" not in row
    assert row["max_budget_usd"] == 0.5
    totals = pipeline.summarize_invocations(as_run(state))
    (item,) = totals["over_budget"]
    assert item["cost_usd"] == 0.42
    assert item["max_budget_usd"] == 0.5
    assert item["outcome"] == "stopped_at_limit"
    (line,) = pipeline.over_budget_lines(totals)
    assert line.startswith("上限で停止:")
    assert "0.4200 USD（上限 0.5 USD）" in line


def test_old_over_budget_flag_is_normalized_at_the_read_boundary() -> None:
    old = {
        "role": "discovery",
        "split_index": 0,
        "cost_usd": 0.9,
        "over_budget": True,
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "duration_ms": 1,
        "num_turns": 1,
        "is_error": False,
        "terminal_reason": None,
        "permission_denials": 0,
        "max_budget_usd": 0.5,
    }
    clean = {**old, "over_budget": False, "cost_usd": 0.1}
    runs = pipeline.load_runs({"micro": {"invocations": [old, clean]}})
    run = next(iter(runs.groups.values()))
    assert isinstance(run, pipeline.ShardedGroupRun)
    assert run.invocations is not None
    flagged, unflagged = run.invocations
    assert flagged.budget_outcome is BudgetOutcome.EXCEEDED_AFTER_SUCCESS
    assert unflagged.budget_outcome is None
    assert "over_budget" not in flagged.to_json()
    assert "over_budget" not in unflagged.to_json()
    (item,) = pipeline.summarize_invocations(run)["over_budget"]
    assert item["outcome"] == "exceeded_after_success"


def test_old_failure_record_without_a_limit_is_read_with_none() -> None:
    failure = DiscoveryLaunchFailed(
        0,
        "discovery",
        claude.FailureKind.TIMEOUT,
        "r",
        1,
        Disposition.NOT_RERUN,
        invocation=FailedInvocation(claude.failure_record(None, 0.5), "v", 10),
    )
    stored = failure.as_record()
    del stored["invocation"]["max_budget_usd"]
    read = failure_from_record(stored)
    assert read.invocation is not None
    assert isinstance(read.invocation.record, claude.StoredInvocationRecord)
    assert read.invocation.record.max_budget_usd is None
    assert "max_budget_usd" not in read.invocation.record.as_row()


def test_stopped_and_exceeded_are_distinct_in_one_run(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    stopped = pipeline.summarize_invocations(as_run(_runs(review)["macro"]))["over_budget"]
    assert [item["outcome"] for item in stopped] == ["stopped_at_limit"]
    exceeded = replace(claude.failure_record(None, 0.5), cost=Measured(0.9))
    assert exceeded.exceeded_after_success()
    at_limit = replace(exceeded, cost=Measured(0.5))
    assert not at_limit.exceeded_after_success()
    unlimited = claude.StoredInvocationRecord.from_row(
        exceeded.as_row() | {"max_budget_usd": None}
    )
    assert not unlimited.exceeded_after_success()


def test_failure_of_another_kind_is_not_marked_over_budget(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    error = claude.ClaudeInvocationError(
        "timeout", claude.failure_record(None, 0.5), claude.FailureKind.TIMEOUT
    )
    _fail(review, error)
    (row,) = [item.to_json() for item in pipeline.all_invocations(as_run(_runs(review)["macro"]))]
    assert "budget_outcome" not in row


def test_failure_without_a_reported_cost_carries_the_limit(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(None))
    (record,) = _runs(review)["macro"]["launch_failures"]
    assert record["cost_source"] == "limit_substitute"
    assert record["cost_usd"] == 0.5


def test_second_failure_keeps_the_first_and_usage_counts_both(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    _fail(review, _budget_error(0.3))
    state = _runs(review)["macro"]
    assert [r["cost_usd"] for r in state["launch_failures"]] == [0.42, 0.3]
    assert pipeline.summarize_invocations(as_run(state))["cost_usd"] == pytest.approx(0.72)


def test_failure_after_a_success_keeps_the_earlier_failures_too(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    _fail(review, _budget_error(0.42))
    _fail(review, _budget_error(0.3))
    state = _runs(review)["macro"]
    assert RunRecordKind.of_record(state) is RunRecordKind.SUCCEEDED_THEN_FAILED
    assert len(state["launch_failures"]) == 2


def test_failure_record_substitutes_the_limit_only_when_nothing_was_reported() -> None:
    assert claude.failure_record(None, 0.5).cost == LimitSubstitute(0.5)
    assert claude.failure_record({"total_cost_usd": 0.42}, 0.5).cost == Measured(0.42)


def test_earlier_failure_cost_survives_a_later_success(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    state = _runs(review)["macro"]
    assert RunRecordKind.of_record(state) is RunRecordKind.SUCCEEDED
    # 前の失敗は前の回として一覧に残り、画面には出ない
    assert [r["round"] for r in state["launch_failures"]] == ["earlier"]
    assert views_of(state) == ()
    succeeded = sum(row["cost_usd"] for row in state["invocations"])
    assert pipeline.summarize_invocations(as_run(state))["cost_usd"] == pytest.approx(
        succeeded + 0.42
    )
    capsys.readouterr()
    assert main(["usage", "shop:p", "--json"]) == EXIT_OK
    total = next(
        row for row in json.loads(capsys.readouterr().out)["rows"] if row["role"] == "合計"
    )
    assert total["cost_usd"] == pytest.approx(succeeded + 0.42)


def test_earlier_failure_cost_survives_success_then_another_failure(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    succeeded = sum(row["cost_usd"] for row in _runs(review)["macro"]["invocations"])
    _fail(review, _budget_error(0.3))
    state = _runs(review)["macro"]
    assert pipeline.summarize_invocations(as_run(state))["cost_usd"] == pytest.approx(
        succeeded + 0.72
    )


def test_failure_success_failure_shows_only_the_last_failure_and_counts_both(
    project: Path,
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    _fail(review, _budget_error(0.3))
    state = _runs(review)["macro"]
    assert [r["round"] for r in state["launch_failures"]] == ["earlier", "current"]
    assert len(views_of(state)) == 1
    succeeded = sum(row["cost_usd"] for row in state["invocations"])
    assert pipeline.summarize_invocations(as_run(state))["cost_usd"] == pytest.approx(
        succeeded + 0.72
    )


def test_failure_before_any_api_call_is_not_charged() -> None:
    stopped = {"num_turns": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}
    assert claude.failure_record(stopped, 0.5).cost == NotCharged()
    # 往復の有無が分からない結果や、トークンを使った結果は上限の額で代える
    assert claude.failure_record({"usage": {}}, 0.5).cost == LimitSubstitute(0.5)
    spent = {"num_turns": 0, "usage": {"input_tokens": 9}}
    assert claude.failure_record(spent, 0.5).cost == LimitSubstitute(0.5)
    # 金額の報告がある結果は、その額を使う
    reported = {"num_turns": 0, "total_cost_usd": 0.1, "usage": {}}
    assert claude.failure_record(reported, 0.5).cost == Measured(0.1)
    assert claude.failure_record(None, 0.5).cost == LimitSubstitute(0.5)


def test_not_charged_counts_as_zero_and_apart_from_the_estimate() -> None:
    totals = total_by_source([Measured(0.5), LimitSubstitute(0.25), NotCharged()])
    assert totals.usd == 0.75
    assert totals.limit_substitute == 0.25
    assert "課金なし" in cost_text(NotCharged())
    assert "推定" in cost_text(LimitSubstitute(0.25))


def test_limit_substitute_reads_and_not_charged_round_trips() -> None:
    assert cost_from_record({"cost_usd": 0.5, "cost_source": "limit_substitute"}) == (
        LimitSubstitute(0.5)
    )
    record = cost_as_record(NotCharged())
    assert record == {"cost_usd": 0.0, "cost_source": "not_charged"}
    assert cost_from_record(record) == NotCharged()


def _stored_failure(cost: Cost, failure_round: FailureRound, index: int = 0) -> StoredFailure:
    record = replace(claude.failure_record(None, 0.5), cost=cost)
    return DiscoveryLaunchFailed(
        index=index,
        role="discovery",
        kind=BUDGET,
        reason="理由",
        attempt=1,
        disposition=Disposition.NOT_RERUN,
        invocation=FailedInvocation(record, "v1", 10),
        round=failure_round,
    )


def test_earlier_failures_summarize_count_and_cost_with_the_estimate(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _fail(review, _budget_error(0.42))
    _fail(review, _budget_error(None))
    macro.run_macro(AssemblyInput.load(review), settings_for(MacroFake([]), max_budget_usd=0.5))
    capsys.readouterr()
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    run = json.loads(capsys.readouterr().out)["run"]
    assert run["failures"] == []
    earlier = run["earlier_failures"]
    assert earlier["count"] == 1
    assert earlier["invocation_count"] == 2
    assert earlier["cost_usd"] == pytest.approx(0.92)
    assert earlier["substituted_cost_usd"] == 0.5
    assert earlier["text"] == "前の回: 通らなかった失敗 1 件、計 0.92 USD（うち推定 0.50 USD）"
    assert len(earlier["failures"]) == 1
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    shown = capsys.readouterr().out
    assert "前の回: 通らなかった失敗 1 件、計 0.92 USD（うち推定 0.50 USD）" in shown
    assert "  上限で失敗" in shown


def test_earlier_failures_omit_the_estimate_and_count_not_charged_as_zero() -> None:
    measured = _stored_failure(Measured(0.3), FailureRound.EARLIER)
    free = _stored_failure(NotCharged(), FailureRound.EARLIER, index=1)
    summary = earlier_failures([measured, free])
    assert summary is not None
    assert summary.text == "前の回: 通らなかった失敗 2 件、計 0.30 USD"
    assert summary.to_json()["substituted_cost_usd"] == 0
    # 今回の回の失敗だけなら、前の回の行は無い
    assert earlier_failures([_stored_failure(Measured(0.3), FailureRound.CURRENT)]) is None


def test_earlier_failures_count_the_recovered_launches_the_cost_includes() -> None:
    recovered = replace(
        _stored_failure(Measured(0.4), FailureRound.EARLIER), disposition=Disposition.RECOVERED
    )
    failed = _stored_failure(Measured(0.3), FailureRound.EARLIER, index=1)
    summary = earlier_failures([recovered, failed])
    assert summary is not None
    assert summary.text == "前の回: 通らなかった失敗 1 件、回復した起動 1 回、計 0.70 USD"


def test_earlier_failures_keep_the_row_when_only_recovered_launches_were_charged() -> None:
    recovered = replace(
        _stored_failure(Measured(0.4), FailureRound.EARLIER), disposition=Disposition.RECOVERED
    )
    summary = earlier_failures([recovered])
    assert summary is not None
    assert summary.text == "前の回: 回復した起動 1 回、計 0.40 USD"
    free = replace(
        _stored_failure(NotCharged(), FailureRound.EARLIER), disposition=Disposition.RECOVERED
    )
    assert earlier_failures([free]) is None


def test_earlier_failures_show_the_failure_without_an_invocation_record() -> None:
    measured = _stored_failure(Measured(0.3), FailureRound.EARLIER)
    unrecorded = replace(
        _stored_failure(NotCharged(), FailureRound.EARLIER, index=1), invocation=None
    )
    summary = earlier_failures([measured, unrecorded])
    assert summary is not None
    assert summary.text == "前の回: 通らなかった失敗 2 件、計 0.30 USD、額の記録なし 1 件"
    encoded = summary.to_json()
    assert (encoded["count"], encoded["invocation_count"]) == (2, 1)
    assert (encoded["recovered_count"], encoded["unrecorded_cost_count"]) == (0, 1)
    # 記録の無い失敗だけでも、隠さずに行を出す
    only = earlier_failures([unrecorded])
    assert only is not None
    assert only.text == "前の回: 通らなかった失敗 1 件、額の記録なし 1 件"


def test_earlier_failures_count_an_unrecorded_recovery_in_both_counts() -> None:
    # 記録の無い RECOVERED は、回復した起動と額の記録なしの両方に入る
    recovered = replace(
        _stored_failure(NotCharged(), FailureRound.EARLIER),
        disposition=Disposition.RECOVERED,
        invocation=None,
    )
    summary = earlier_failures([recovered])
    assert summary is not None
    assert summary.text == "前の回: 回復した起動 1 回、額の記録なし 1 件"
    encoded = summary.to_json()
    assert (encoded["count"], encoded["invocation_count"]) == (0, 0)
    assert (encoded["recovered_count"], encoded["unrecorded_cost_count"]) == (1, 1)
