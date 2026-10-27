from typing import Any

from fakes import as_run

from jigane.commands.subcommands.run import failure_reason_lines, retry_lines
from jigane.skills.pipeline import over_budget_lines


def record(
    stage: str, index: int, kind: str, reason: str, attempt: int, disposition: str
) -> dict[str, Any]:
    return {
        "stage": stage,
        "index": index,
        "role": "discovery" if stage == "discovery" else "judge",
        "kind": kind,
        "reason": reason,
        "attempt": attempt,
        "disposition": disposition,
        "scope": "launch",
        "round": "current",
        "ran_at": None,
    }


def test_retry_line_appears_only_when_a_split_was_rerun() -> None:
    assert retry_lines(as_run({"launch_failures": []}).launch_failures or ()) == []
    assert retry_lines(as_run({}).launch_failures or ()) == []
    state = {
        "launch_failures": [
            record("verification", 0, "overloaded", "529", 1, "recovered"),
            record("verification", 1, "timeout", "t", 1, "rerun_failed"),
            record("verification", 1, "timeout", "t", 2, "not_rerun"),
            record("discovery", 3, "timeout", "t", 1, "not_rerun"),
        ]
    }
    assert retry_lines(as_run(state).launch_failures or ()) == [
        "走らせ直した検証の分割: 2 件（一時の失敗）"
    ]


def test_failure_lines_show_only_the_final_failure_of_each_launch() -> None:
    state = {
        "launch_failures": [
            record("discovery", 2, "bad_output", "schema", 1, "not_rerun"),
            record("verification", 0, "overloaded", "529", 1, "recovered"),
            record("verification", 1, "timeout", "first", 1, "rerun_failed"),
            record("verification", 1, "timeout", "second", 2, "not_rerun"),
        ],
    }
    assert failure_reason_lines(as_run(state).launch_failures or ()) == [
        "  発見役 2（bad_output）: schema",
        "  検証の分割 1・judge（timeout、2 回目）: second",
    ]


def test_input_stops_are_shown_once_from_budget_stops_for_both_stages() -> None:
    stop = {
        "estimated_input_tokens": 900,
        "estimated_input_usd": 1.5,
        "max_budget_usd": 1.0,
    }
    state = {
        "launch_failures": [],
        "budget_stops": [
            {**stop, "role": "discovery", "index": 0},
            {**stop, "role": "rebuttal", "index": 1},
        ],
    }
    # 失敗の理由の行には出ず、起動せずの行にだけ、発見役と検証役が同じ形で出る
    assert failure_reason_lines(as_run(state).launch_failures or ()) == []
    lines = over_budget_lines({"over_budget": [], "budget_stops": state["budget_stops"]})
    assert len(lines) == 2
    assert all(line.startswith("起動せず: ") for line in lines)


def test_missing_candidate_reruns_are_reported_apart_from_split_reruns() -> None:
    refill = {
        **record("verification", 1, "bad_output", "index [1] が無い", 2, "rerun_failed"),
        "scope": "missing_candidates",
        "candidates": [],
    }
    # 同じ分割に NOT_RERUN の起動の失敗があっても、欠けた候補の失敗は理由を落とさない
    ended = record("verification", 1, "timeout", "t", 2, "not_rerun")
    state = {"launch_failures": [refill, ended]}
    assert retry_lines(as_run(state).launch_failures or ()) == [
        "欠けた候補だけ走らせ直した検証の役: 1 件"
    ]
    assert failure_reason_lines(as_run(state).launch_failures or ()) == [
        "  検証の分割 1・judge（bad_output、欠けた候補の走らせ直し）: index [1] が無い",
        "  検証の分割 1・judge（timeout、2 回目）: t",
    ]
