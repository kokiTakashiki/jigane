"""方針の群の、一回の起動の結果の型。macro が作り、policy_keep が図の起動の判定に読む。"""

from dataclasses import dataclass
from typing import Any

from jigane.findings.discard import Considered
from jigane.groups.definitions import MACRO
from jigane.llm_runs.launch_failure import DiscoveryLaunchFailed, StoredInvocation
from jigane.skills import discovery_errors, launch_index, pipeline
from jigane.skills.launch_index import LaunchKind


class LaunchFailed(discovery_errors.DiscoveryError):
    """方針の一回の起動が失敗した。実行の記録に残す失敗（額、出所、起動の記録を含む）を運ぶ。"""

    def __init__(self, message: str, failure: DiscoveryLaunchFailed) -> None:
        super().__init__(MACRO, message)
        self.failure = failure


@dataclass(frozen=True)
class ExtraLaunch:
    """図の起動に加えて走らせる、一つの原則の起動。viewpoint はその原則の番号（"P1" など）。"""

    viewpoint: str
    prompt: str


@dataclass(frozen=True)
class DiagramLaunch:
    """図の起動。"""


@dataclass(frozen=True)
class PrincipleLaunch:
    """原則の起動。ordinal は原則の起動の並びの位置（1 から）で、run_macro が一度だけ付ける。"""

    extra: ExtraLaunch
    ordinal: int


type Launch = DiagramLaunch | PrincipleLaunch


def stored_index(launch: Launch) -> int:
    """起動の記録、見送りの記録、見積もりの検査が使う、起動の通し番号。"""
    match launch:
        case DiagramLaunch():
            return launch_index.to_index(LaunchKind.DIAGRAM, 0)
        case PrincipleLaunch():
            return launch_index.to_index(LaunchKind.PRINCIPLE, launch.ordinal)


@dataclass(frozen=True)
class Stopped:
    """入力が上限を超え、起動しなかった一本。"""

    launch: Launch
    error: discovery_errors.InputBudgetError


@dataclass(frozen=True)
class Outcome:
    """結果の処理の前に出すもの。final は区分を付ける前の最終の指摘。"""

    final: tuple[dict[str, Any], ...]
    invocations: tuple[StoredInvocation, ...]
    considered: Considered
    notice: str | None = None


type LaunchResult = tuple[Outcome, pipeline.DropCounts] | LaunchFailed | Stopped
