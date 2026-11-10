"""方針の群で、図の起動が走らなかった回の判定と、前の結果を残す選択。

呼ぶ側（macro）は起動の結果の並びを渡すだけで、判定、記録の段の列、残す結果の選択はここが持つ。
runs.json の記録の組み立てと書き込みは policy_record、理由の読み取りは policy_reason が持つ。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import assert_never

from jigane.findings.records import TriagedFinding, load_findings_state
from jigane.groups.definitions import MACRO
from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs.launch_failure import DiscoveryLaunchFailed, StoredInvocation
from jigane.reviews.lifecycle import Review
from jigane.skills import policy_record
from jigane.skills.input_budget import BudgetStop
from jigane.skills.launch_index import LaunchKind, kind_of, role_of_kind
from jigane.skills.policy_launch import LaunchFailed, LaunchResult, Stopped
from jigane.skills.policy_reason import DiagramNotRun

# --- 判定 ---


@dataclass(frozen=True)
class Classified:
    """図の起動の判定と、記録に書く材料。記録の段への組み立ては policy_record が行う。"""

    # 図の起動が走ったなら None
    reason: DiagramNotRun | None
    failures: tuple[DiscoveryLaunchFailed, ...]
    stops: tuple[BudgetStop, ...]
    # 結果を捨てる成功済みの起動。払った額を記録に残すため
    paid: tuple[StoredInvocation, ...]


def _is_diagram(shape: PolicyShape, index: int | None) -> bool:
    """図の形で、保存した番号が図の起動のものか。他の形の起動は、番号を問わず図の起動ではない。"""
    kind = kind_of(index)
    match kind:
        case LaunchKind.DIAGRAM:
            return shape is PolicyShape.DIAGRAM
        case LaunchKind.PRINCIPLE | None:
            return False
        case _:
            assert_never(kind)


def classify_launches(
    context: policy_record.RunContext, results: Sequence[LaunchResult]
) -> Classified:
    """起動の結果の並びから、図の起動の判定を一度だけ行い、記録に書く材料を同じ判定から作る。

    図の起動と見るのは、図の形（context.shape が DIAGRAM）で、保存した番号が図の起動のもの。
    他の形（設計書の形など）の起動は、行を書き換えず、他の起動と同じに扱う。
    図の起動の失敗の行は role が DIAGRAM になる。
    これを記録から読む policy_reason.diagram_not_run が同じ値を導くのは、前の結果がある回に
    限る。前の結果が無い回（初回）の記録は、残すものが無いので、読むと None になる。
    """
    failures: list[DiscoveryLaunchFailed] = []
    stops: list[BudgetStop] = []
    reason: DiagramNotRun | None = None
    for result in results:
        match result:
            case LaunchFailed():
                failure = result.failure
                if _is_diagram(context.shape, failure.index):
                    failure = replace(failure, role=role_of_kind(LaunchKind.DIAGRAM).value)
                    reason = DiagramNotRun.FAILED
                failures.append(failure)
            case Stopped():
                stop = result.error.stop
                if _is_diagram(context.shape, stop.index):
                    reason = DiagramNotRun.STOPPED_FOR_INPUT
                stops.append(stop)
            case _:
                pass  # 走った起動（結果の組）は、走らなかった起動の材料ではない
    paid = tuple(i for r in results if isinstance(r, tuple) for i in r[0].invocations)
    return Classified(reason, tuple(failures), tuple(stops), paid)


# --- 残す結果の選択 ---


def previous_findings(review: Review) -> tuple[TriagedFinding, ...]:
    """走らなかった起動が結果を置き換えない回に、残す前の結果を選ぶ。

    原則の起動だけの結果で方針の群を置き換えると、前の回の図の起動が出した指摘（必読を含む）が
    消えるため。前の結果が無い回（初回など）は残すものが無く、指摘は空になる。
    成功した起動の結果は捨てる（払った額は Classified.paid が持ち、記録に残る）。
    """
    return load_findings_state(review.id.directory()).groups.get(MACRO, ())
