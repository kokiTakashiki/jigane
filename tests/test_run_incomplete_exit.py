import json
from dataclasses import replace
from pathlib import Path

import pytest
from fakes import (
    MacroFailure,
    MacroFake,
    MicroFake,
    clear_policy,
    discovered,
    settings_for,
    with_budget,
)

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_INCOMPLETE_COVERAGE, EXIT_OK
from jigane.commands.subcommands.run import (
    HistoryNotice,
    PolicyGroupResult,
    ShardedGroupResult,
    print_group_result,
)
from jigane.llm_runs import claude
from jigane.llm_runs.launch_failure import (
    DiscoveryLaunchFailed,
    Disposition,
    FailureRound,
)
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro, micro, policy_launch
from jigane.skills.assembly import AssemblyInput
from jigane.skills.input_budget import StoredBudgetStop
from jigane.skills.pipeline import run_gaps

NO_HISTORY = HistoryNotice((), ())
HUGE_PROMPT = "x" * 5_000_000
TURNS = claude.FailureKind.TURNS_EXCEEDED


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


class TurnsExceededFake(MacroFake):
    """原則の起動を turns_exceeded で失敗させる。"""

    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        try:
            return super().__call__(invocation)
        except claude.ClaudeInvocationError as error:
            raise claude.ClaudeInvocationError(
                "最大ターン数に達しました", error.record, TURNS
            ) from None


@pytest.mark.usefixtures("all_principle_shapes")
def test_policy_run_with_a_turns_exceeded_launch_exits_3_and_writes_the_error_line(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    fake = TurnsExceededFake([discovered("M4")], fails=MacroFailure.PRINCIPLES)
    assert main(["run", "shop:p", "macro"], invoke=fake) == EXIT_INCOMPLETE_COVERAGE
    err_lines = [line for line in capsys.readouterr().err.splitlines() if "turns_exceeded" in line]
    assert err_lines
    assert all(line.startswith("jigane: ") for line in err_lines)


def test_policy_run_with_a_budget_stop_exits_3(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M4")])
    settings = with_budget(settings_for(fake, max_budget_usd=0.5), model="claude-sonnet-5-5")
    outcome = macro.run_macro(
        AssemblyInput.load(review),
        settings,
        extras=[
            policy_launch.ExtraLaunch("P1", "短い"),
            policy_launch.ExtraLaunch("P2", HUGE_PROMPT),
        ],
    )
    assert outcome.run_state.budget_stops
    result = PolicyGroupResult(outcome, review, NO_HISTORY)
    assert print_group_result(result, as_json=False) == EXIT_INCOMPLETE_COVERAGE
    assert "jigane: 原則 P2 の起動を上限のために見送りました" in capsys.readouterr().err


def test_sharded_run_with_a_verification_stop_exits_3(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    outcome = micro.run_micro(
        AssemblyInput.load(review), settings_for(MicroFake({1: []}), max_budget_usd=0.5)
    )
    assert outcome.failed_shards == 0
    assert outcome.failed_splits == 0
    assert not run_gaps(outcome.run_state)
    assert ShardedGroupResult("実装", outcome, None, NO_HISTORY).exit_code == EXIT_OK
    # 検証役を見送った記録だけがある回。分割の失敗には数えられていない
    stop = StoredBudgetStop("rebuttal", 0, 100, 0.01, 0.001)
    stopped = replace(outcome, run_state=replace(outcome.run_state, budget_stops=(stop,)))
    result = ShardedGroupResult("実装", stopped, None, NO_HISTORY)
    assert result.exit_code == EXIT_INCOMPLETE_COVERAGE


def test_failed_shard_and_verification_stop_in_one_run_write_the_stop_line(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    outcome = micro.run_micro(
        AssemblyInput.load(review), settings_for(MicroFake({1: []}), max_budget_usd=0.5)
    )
    stop = StoredBudgetStop("rebuttal", 0, 100, 0.01, 0.001)
    state = replace(outcome.run_state, shards=2, succeeded_shards=1, budget_stops=(stop,))
    result = ShardedGroupResult("実装", replace(outcome, run_state=state), None, NO_HISTORY)
    assert result.outcome.failed_shards == 1
    capsys.readouterr()
    assert print_group_result(result, as_json=False) == EXIT_INCOMPLETE_COVERAGE
    err = capsys.readouterr().err
    assert "発見役が失敗したシャード 1 個" in err
    assert err.count("jigane: 起動せず: ") == 1


def _policy_result_with(project: Path, failure: DiscoveryLaunchFailed) -> PolicyGroupResult:
    review = create_feature_review(project, ["src"], "p")
    outcome = macro.run_macro(
        AssemblyInput.load(review), settings_for(MacroFake([discovered("M4")]))
    )
    state = replace(outcome.run_state, launch_failures=(failure,))
    return PolicyGroupResult(replace(outcome, run_state=state), review, NO_HISTORY)


def test_policy_run_carrying_only_earlier_failures_exits_0(project: Path) -> None:
    failure = DiscoveryLaunchFailed(
        0, "diagram", TURNS, "r", 1, Disposition.NOT_RERUN, round=FailureRound.EARLIER
    )
    assert _policy_result_with(project, failure).exit_code == EXIT_OK


def test_policy_run_with_only_recovered_failures_exits_0(project: Path) -> None:
    failure = DiscoveryLaunchFailed(0, "diagram", TURNS, "r", 1, Disposition.RECOVERED)
    assert _policy_result_with(project, failure).exit_code == EXIT_OK


def test_estimate_json_passes_with_a_past_run_that_had_budget_stops(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    assert main(["run", str(review.id), "micro"], invoke=MicroFake({1: []})) == EXIT_OK
    runs_path = review.id.directory() / "runs.json"
    runs = json.loads(runs_path.read_text(encoding="utf-8"))
    runs["micro"]["budget_stops"] = [StoredBudgetStop("rebuttal", 0, 100, 0.01, 0.001).as_record()]
    runs_path.write_text(json.dumps(runs), encoding="utf-8")
    capsys.readouterr()
    assert main(["estimate", str(review.id), "micro", "--json"]) == EXIT_OK
    past = json.loads(capsys.readouterr().out)["past_run"]
    assert past["budget_stops"][0]["estimated_input_tokens"] == 100


def test_run_json_writes_budget_stops_in_totals_as_records(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M4")])
    settings = with_budget(settings_for(fake, max_budget_usd=0.5), model="claude-sonnet-5-5")
    outcome = macro.run_macro(
        AssemblyInput.load(review),
        settings,
        extras=[
            policy_launch.ExtraLaunch("P1", "短い"),
            policy_launch.ExtraLaunch("P2", HUGE_PROMPT),
        ],
    )
    result = PolicyGroupResult(outcome, review, NO_HISTORY)
    capsys.readouterr()
    assert print_group_result(result, as_json=True) == EXIT_INCOMPLETE_COVERAGE
    [stop] = json.loads(capsys.readouterr().out)["totals"]["budget_stops"]
    assert stop["index"] == 2
    assert stop["estimated_input_tokens"] > 0
    assert stop["max_budget_usd"] == 0.5
