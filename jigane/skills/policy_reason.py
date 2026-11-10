"""保存した方針の記録から、図の起動が走らず方針の指摘を置き換えなかった理由を読む。

理由を決めるのはこのモジュールの `diagram_not_run` だけで、run と show の表示はその戻り値を引く。
"""

from enum import StrEnum

from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs.launch_failure import RunRecordKind, current_failures
from jigane.skills import pipeline
from jigane.skills.launch_index import LaunchKind, kind_of, role_of_kind


class DiagramNotRun(StrEnum):
    """図の起動が走らなかった理由。"""

    FAILED = "failed"
    STOPPED_FOR_INPUT = "stopped_for_input"


def diagram_not_run(run: pipeline.PolicyGroupRun | None) -> DiagramNotRun | None:
    """方針の記録から理由を一度だけ読む。置き換えなかった回でなければ None。

    当たるのは、図の形の、前の結果に失敗か見送りを足した記録。前の結果が無い回の記録は
    当たらない（残すものが無いため）。図の起動は失敗か見送りのどちらかなので、理由は一つに決まる。
    """
    if run is None or run.shape is None or run.record_kind is None:
        return None
    if PolicyShape(run.shape) is not PolicyShape.DIAGRAM:
        return None
    if RunRecordKind(run.record_kind) is not RunRecordKind.SUCCEEDED_THEN_FAILED:
        return None
    reasons = [
        *(
            DiagramNotRun.FAILED
            for failure in current_failures(run.launch_failures or ())
            if failure.role == role_of_kind(LaunchKind.DIAGRAM)
        ),
        *(
            DiagramNotRun.STOPPED_FOR_INPUT
            for stop in run.budget_stops or ()
            if kind_of(stop.index) is LaunchKind.DIAGRAM
        ),
    ]
    return reasons[0] if reasons else None
