import json
from pathlib import Path
from typing import Any

import pytest
from ast_support import json_users_of_stored_file

import jigane
from jigane.groups.definitions import MACRO, MICRO
from jigane.llm_runs import claude
from jigane.llm_runs.cost import Measured
from jigane.llm_runs.launch_failure import (
    DiscoveryLaunchFailed,
    Disposition,
    FailureRound,
    StoredInvocation,
)
from jigane.llm_runs.prompt_stamp import PromptStamp
from jigane.skills.pipeline import RUNS_FORMAT, RunsFile, ShardedGroupRun, write_runs

ROW: dict[str, Any] = {
    "role": "discovery",
    "split_index": 0,
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
    "cost_usd": 0.25,
    "cost_source": "measured",
    "prompt_version": "v",
    "prompt_chars": 10,
}


def _failure(reason: str) -> DiscoveryLaunchFailed:
    return DiscoveryLaunchFailed(
        0, "discovery", claude.FailureKind.TIMEOUT, reason, 1, Disposition.NOT_RERUN
    )


def test_runs_file_round_trips_through_the_file(tmp_path: Path) -> None:
    stored = {
        "macro": {
            "ran_at": "t",
            "invocations": [{**ROW, "rerun": "split", "extra": 1}],
            "launch_failures": [_failure("a").as_record()],
            "budget_stops": [],
        },
        "micro": {"discovered": 1},
    }
    assert RunsFile.from_json(stored).to_json() == stored

    write_runs(tmp_path, RunsFile.from_json(stored).groups)

    assert json.loads((tmp_path / "runs.json").read_text("utf-8")) == {
        "format": RUNS_FORMAT,
        **stored,
    }


def test_old_invocation_row_without_a_limit_or_prompt_is_read_and_written_unchanged() -> None:
    old = {k: v for k, v in ROW.items() if k not in ("max_budget_usd", "prompt_version")}
    row = StoredInvocation.from_json(old)
    assert row.record.max_budget_usd is None
    assert row.stamp is None
    assert row.to_json() == old


@pytest.mark.parametrize(
    "dropped", [("prompt_chars",), ("prompt_version", "prompt_chars"), ("prompt_version",)]
)
def test_old_invocation_row_with_a_missing_prompt_key_round_trips(
    dropped: tuple[str, ...],
) -> None:
    old = {k: v for k, v in ROW.items() if k not in dropped}
    row = StoredInvocation.from_json(old)
    assert row.stamp is None
    assert row.to_json() == old


def test_invocation_row_with_both_prompt_keys_reads_as_a_stamp() -> None:
    row = StoredInvocation.from_json(ROW)
    assert row.stamp == PromptStamp("v", 10)
    assert row.to_json() == ROW


def test_next_round_moves_failures_failed_then_succeeded_then_failed() -> None:
    first = ShardedGroupRun.from_json({"launch_failures": [_failure("first").as_record()]})
    succeeded = ShardedGroupRun.from_json({"ran_at": "second"})

    after_success = first.next_round(succeeded)
    assert after_success.launch_failures is not None
    assert [(f.reason, f.round) for f in after_success.launch_failures] == [
        ("first", FailureRound.EARLIER)
    ]
    assert after_success.current_failures == []

    failed_again = ShardedGroupRun.from_json({"launch_failures": [_failure("third").as_record()]})
    final = after_success.next_round(failed_again)
    assert final.launch_failures is not None
    assert [(f.reason, f.round) for f in final.launch_failures] == [
        ("first", FailureRound.EARLIER),
        ("third", FailureRound.CURRENT),
    ]
    assert [f.reason for f in final.current_failures] == ["third"]


def test_next_round_without_earlier_failures_returns_the_new_run() -> None:
    new = ShardedGroupRun.from_json({"ran_at": "x"})
    assert ShardedGroupRun.from_json({"ran_at": "y"}).next_round(new) is new


def test_budget_outcome_comes_from_the_record_type() -> None:
    exceeded = claude.InvocationRecord(
        1,
        1,
        0,
        0,
        Measured(0.9),
        1,
        1,
        is_error=False,
        terminal_reason=None,
        permission_denials=0,
        max_budget_usd=0.5,
    )
    within = claude.InvocationRecord(
        1,
        1,
        0,
        0,
        Measured(0.5),
        1,
        1,
        is_error=False,
        terminal_reason=None,
        permission_denials=0,
        max_budget_usd=0.5,
    )
    assert exceeded.exceeded_after_success()
    assert not within.exceeded_after_success()
    stored = claude.StoredInvocationRecord.from_row({**ROW, "cost_usd": 0.9})
    assert stored.exceeded_after_success()


def test_runs_json_is_read_and_written_only_in_pipeline() -> None:
    """runs.json を json で直接読み書きするのは pipeline.py だけ。"""
    root = Path(jigane.__file__).parent
    offenders = json_users_of_stored_file(
        root, "RUNS_FILE", "runs.json", exclude=frozenset({"pipeline.py"})
    )
    assert offenders == []


def test_groups_are_independent_keys() -> None:
    runs = RunsFile.from_json({"macro": {}, "micro": {}})
    assert set(runs.groups) == {MACRO, MICRO}


def test_sharded_record_with_only_some_sharded_items_round_trips() -> None:
    stored = {"shards": 3, "succeeded_shards": 2}

    assert ShardedGroupRun.from_json(stored).to_json() == stored
