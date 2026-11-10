"""runs.json の方針の記録を、走らなかった起動について組み立てて書く。

記録の段は、走らなかった起動の理由ごとに、方針の記録へ一つ足す。段の列は呼ぶ側が一度だけ作り、
write_unfinished が前の記録の上に重ねて保存する。
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from jigane.groups.definitions import MACRO
from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude
from jigane.llm_runs.launch_failure import DiscoveryLaunchFailed, RunRecordKind, StoredInvocation
from jigane.reviews.lifecycle import Review
from jigane.skills import pipeline
from jigane.skills.input_budget import BudgetStop
from jigane.storage.locking import review_lock

# 方針の記録に、走らなかった起動の理由を一つ足す段
type RecordStep = Callable[[pipeline.PolicyGroupRun], pipeline.PolicyGroupRun]


@dataclass(frozen=True)
class RunContext:
    """記録を作る実行の文脈。shape は方針の計画から取る。"""

    review: Review
    settings: claude.RunSettings
    started: float
    shape: PolicyShape


def failures_step(failures: Sequence[DiscoveryLaunchFailed]) -> RecordStep:
    """失敗した起動を launch_failures に足す。前の失敗は保存の側が前の回として積む。"""
    return lambda run: replace(run, launch_failures=tuple(failures))


def stops_step(stops: Sequence[BudgetStop]) -> RecordStep:
    """入力の見積もりで見送った起動を budget_stops に足す。前の回の見送りは持ち越さない。"""
    return lambda run: replace(run, budget_stops=tuple(stop.stored() for stop in stops) or None)


def paid_step(paid: Sequence[StoredInvocation]) -> RecordStep:
    """結果を捨てた成功済みの起動を invocations に足す。払った額を usage が数えるため。"""
    return lambda run: replace(run, invocations=tuple(paid))


def unfinished_steps(
    failures: Sequence[DiscoveryLaunchFailed],
    stops: Sequence[BudgetStop],
    paid: Sequence[StoredInvocation],
) -> list[RecordStep]:
    """走らなかった起動の材料から記録の段の列を作る唯一の規則。

    paid が空なら、払った額の段は足さず、前の記録の invocations を残す（成功した起動が無い回）。
    """
    steps = [failures_step(failures), stops_step(stops)]
    return [*steps, paid_step(paid)] if paid else steps


def write_unfinished(context: RunContext, steps: Sequence[RecordStep]) -> pipeline.PolicyGroupRun:
    """走らなかった起動を runs.json の方針の記録に残す。指摘は触らない。

    指摘を生んだ前の回の記録（時刻、捨てた候補、起動）があれば、それを保って steps を重ねる。
    保存の ReplacedRun がこの時刻を前の指摘の回として読むため。
    初回の失敗など、指摘を生んだ回が無ければ、起動を持たない記録に steps を重ねる。
    """
    directory = context.review.id.directory()
    with review_lock(directory):
        runs = pipeline.read_runs(directory)
        previous = runs.get(MACRO)
        if (
            isinstance(previous, pipeline.PolicyGroupRun)
            and RunRecordKind.of_record(previous.to_json()).has_previous_run
        ):
            base = replace(
                previous,
                record_kind=RunRecordKind.SUCCEEDED_THEN_FAILED.value,
                shape=context.shape,
            )
        else:
            base = replace(
                pipeline.run_state_head(
                    pipeline.PolicyGroupRun,
                    settings=context.settings,
                    invocations=[],
                    discovered=0,
                    drops=pipeline.DropCounts(),
                    elapsed_s=time.monotonic() - context.started,
                ),
                record_kind=RunRecordKind.FAILED_ONLY.value,
                shape=context.shape,
            )
        new_run = base
        for step in steps:
            new_run = step(new_run)
        saved = (
            previous.next_round(new_run)
            if isinstance(previous, pipeline.PolicyGroupRun)
            else new_run
        )
        runs[MACRO] = saved
        pipeline.write_runs(directory, runs)
    return saved
