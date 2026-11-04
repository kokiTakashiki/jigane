"""実装のスキル。差分をシャードに分け、シャードごとの発見役を並列に起動し、敵対的検証にかける。"""

from jigane.findings.records import StoredFinding
from jigane.groups import viewpoints
from jigane.groups.definitions import MICRO, GroupId
from jigane.llm_runs import claude
from jigane.skills import (
    macro,
    sharded,
    triage,
)
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import ImplementationEstimate

GROUP = MICRO
VIEWPOINTS = tuple(viewpoint.number for viewpoint in viewpoints.of_group(MICRO))
DISCOVERY = sharded.discovery_spec(GROUP)
DISCOVERY_TOOLS = macro.DISCOVERY_TOOLS
ESTIMATE_NOTE = (
    "実装はシャードごとの発見役に加え、検証の役を起動します。実測の倍率はまだありません。"
)

MicroRun = sharded.ShardedRun


def _select(
    kept: list[StoredFinding],
    last_theme_number: int,
    context: sharded.SelectContext,
) -> sharded.Selection:
    outcome = triage.triage_micro(
        kept,
        last_theme_number,
        working_directory=context.working_directory,
        settings=context.settings,
    )
    return sharded.Selection(
        findings=outcome.findings,
        theme_update=outcome.theme_update,
        records=(outcome.record,) if outcome.record is not None else (),
        run_state_extra={"theme_bundling": outcome.bundling},
    )


def run_micro(assembly: AssemblyInput, settings: claude.RunSettings) -> MicroRun:
    spec = sharded.ShardedGroup(
        group=GROUP,
        spec=DISCOVERY,
        tools=DISCOVERY_TOOLS,
        select=_select,
    )
    return sharded.run_sharded(spec, assembly, settings)


def estimate_micro(assembly: AssemblyInput, group: GroupId) -> ImplementationEstimate:
    return sharded.estimate_sharded(ImplementationEstimate, assembly, group, ESTIMATE_NOTE)
