import json
from typing import Any

import pytest

from jigane.llm_runs.claude import FailureKind
from jigane.llm_runs.launch_failure import (
    CandidatesUnverified,
    DiscoveryLaunchFailed,
    Disposition,
    FailureView,
    StoredFailure,
    VerificationLaunchFailed,
    failure_from_record,
    failure_views,
    rerun_count,
    stopped_rerun_count,
)
from jigane.skills.pipeline import ShardedGroupRun


def read_failures(state: dict[str, Any]) -> list[StoredFailure]:
    return list(ShardedGroupRun.from_json(state).launch_failures or ())


def views_of(state: dict[str, Any]) -> tuple[FailureView, ...]:
    return failure_views(read_failures(state))


def make(
    disposition: Disposition, kind: FailureKind = FailureKind.OVERLOADED
) -> VerificationLaunchFailed:
    return VerificationLaunchFailed(3, "judge", kind, "529", 1, disposition)


def test_record_round_trips_through_runs_json() -> None:
    failures = [make(d) for d in Disposition] + [
        DiscoveryLaunchFailed(
            0, "discovery", FailureKind.BAD_OUTPUT, "x", 1, Disposition.NOT_RERUN
        )
    ]
    stored = json.loads(json.dumps({"launch_failures": [f.as_record() for f in failures]}))
    assert read_failures(stored) == failures


def test_record_stores_enum_values_as_plain_strings() -> None:
    record = make(Disposition.RECOVERED).as_record()
    assert record["kind"] == "overloaded"
    assert record["disposition"] == "recovered"
    assert record["stage"] == "verification"


def test_rerun_count_is_derived_from_the_disposition() -> None:
    failures = [
        make(Disposition.RECOVERED),
        make(Disposition.RERUN_FAILED),
        make(Disposition.NOT_RERUN),
    ]
    assert rerun_count(failures) == 2
    assert rerun_count([]) == 0
    assert read_failures({}) == []


def test_failure_views_carry_kind_reason_and_one_sentence_and_skip_recovered() -> None:
    missing = CandidatesUnverified(
        2,
        "rebuttal",
        FailureKind.BAD_OUTPUT,
        "rebuttal の出力に index [1] が無い",
        2,
        Disposition.RERUN_FAILED,
        candidates=(1,),
    )
    state = {
        "launch_failures": [make(Disposition.RECOVERED).as_record(), missing.as_record()],
    }
    (view,) = views_of(state)
    assert view.to_json() == {
        "stage": "verification",
        "scope": "missing_candidates",
        "index": 2,
        "role": "rebuttal",
        "kind": "bad_output",
        "reason": "rebuttal の出力に index [1] が無い",
        "reruns": 1,
        "stopped_reruns": 0,
        "text": f"{missing.line}、走らせ直し 1 回",
    }


def test_missing_candidates_sentence_names_the_split_and_a_matching_title() -> None:
    missing = CandidatesUnverified(
        2,
        "rebuttal",
        FailureKind.BAD_OUTPUT,
        "欠けた",
        2,
        Disposition.RERUN_FAILED,
        candidates=(1,),
    )
    (view,) = failure_views([missing])
    assert "候補の検証が欠けた（bad_output、分割 2・rebuttal）" in view.text
    assert "起動が失敗" not in view.text


def test_rerun_failure_and_final_failure_are_one_view_with_the_rerun_count() -> None:
    first = VerificationLaunchFailed(
        1,
        "judge",
        FailureKind.OVERLOADED,
        "一回目",
        1,
        Disposition.RERUN_FAILED,
    )
    last = VerificationLaunchFailed(
        1, "judge", FailureKind.OVERLOADED, "最後", 2, Disposition.NOT_RERUN
    )
    other = VerificationLaunchFailed(
        2, "judge", FailureKind.OVERLOADED, "別", 1, Disposition.NOT_RERUN
    )
    state = {"launch_failures": [first.as_record(), last.as_record(), other.as_record()]}
    views = views_of(state)
    assert [view.index for view in views] == [1, 2]
    assert "最後" in views[0].text
    assert "一回目" not in views[0].text
    assert [view.reruns for view in views] == [1, 0]
    assert "走らせ直し 1 回" in views[0].text
    assert "走らせ直し" not in views[1].text


def _missing(role: str, index: int = 2) -> CandidatesUnverified:
    return CandidatesUnverified(
        index,
        role,
        FailureKind.BAD_OUTPUT,
        "欠けた",
        1,
        Disposition.NOT_RERUN,
        candidates=(1,),
    )


def _launch(role: str, index: int = 2) -> VerificationLaunchFailed:
    return VerificationLaunchFailed(
        index, role, FailureKind.OVERLOADED, "起動", 1, Disposition.NOT_RERUN
    )


def _views(*failures: StoredFailure) -> tuple[FailureView, ...]:
    return failure_views(failures)


def test_missing_candidates_of_different_roles_in_one_split_are_separate_views() -> None:
    assert len(_views(_missing("rebuttal"), _missing("judge"))) == 2


def test_launch_failure_and_missing_candidates_of_one_split_are_separate_views() -> None:
    assert len(_views(_launch("judge"), _missing("judge"))) == 2


def test_launch_failures_of_different_roles_in_one_split_are_one_view() -> None:
    (view,) = _views(_launch("rebuttal"), _launch("judge"))
    assert view.role == "judge"


def test_stopped_rerun_is_not_counted_as_a_launched_rerun() -> None:
    stopped = make(Disposition.RERUN_STOPPED)
    launched = make(Disposition.RERUN_FAILED)
    assert rerun_count([stopped]) == 0
    assert stopped_rerun_count([stopped]) == 1
    assert rerun_count([launched]) == 1
    assert stopped_rerun_count([launched]) == 0


def test_stopped_rerun_view_says_it_was_stopped_by_the_estimate() -> None:
    stopped = make(Disposition.RERUN_STOPPED)
    (view,) = failure_views([stopped])
    assert (view.reruns, view.stopped_reruns) == (0, 1)
    assert view.text.endswith("、走らせ直しは見積もりで止めた")
    assert "走らせ直し 1 回" not in view.text
    assert view.to_json()["stopped_reruns"] == 1
    assert read_failures({"launch_failures": [stopped.as_record()]}) == [stopped]


def test_candidates_unverified_round_trips_with_its_candidate_numbers() -> None:
    missing = CandidatesUnverified(
        2,
        "judge",
        FailureKind.BAD_OUTPUT,
        "欠けた",
        2,
        Disposition.RERUN_FAILED,
        candidates=(3, 5),
    )
    read = failure_from_record(json.loads(json.dumps(missing.as_record())))
    assert read == missing
    assert isinstance(read, CandidatesUnverified)
    assert read.candidates == (3, 5)


def test_launch_failed_round_trips_as_the_other_member_of_the_sum() -> None:
    launch = _launch("judge")
    read = failure_from_record(json.loads(json.dumps(launch.as_record())))
    assert read == launch
    assert isinstance(read, VerificationLaunchFailed)


def test_record_whose_stage_and_role_match_no_type_is_refused() -> None:
    base = make(Disposition.NOT_RERUN).as_record()
    for override in (
        {"scope": "missing_candidates", "stage": "discovery"},
        {"scope": "missing_candidates", "role": None},
    ):
        with pytest.raises(ValueError, match="実在する型に当たりません"):
            failure_from_record({**base, **override})
