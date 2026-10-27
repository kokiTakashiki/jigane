"""実装のスキル。差分をシャードに分け、シャードごとの発見役を並列に起動し、敵対的検証にかける。"""

from typing import Any

from pydantic import BaseModel

from jigane.findings.records import StoredFinding
from jigane.groups.definitions import MICRO, GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.finding_model import RoleShape, role_findings_model
from jigane.skills import (
    adversarial,
    macro,
    sharded,
    triage,
)
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import ImplementationEstimate
from jigane.skills.output_models import json_schema_of

GROUP = MICRO
VIEWPOINTS = ("D1", "D2", "D3", "D4", "D6", "D7", "D8", "D9", "D10", "D11", "T1", "T2", "T3", "T4")
DISCOVERY_TOOLS = macro.DISCOVERY_TOOLS
ESTIMATE_NOTE = (
    "実装はシャードごとの発見役に加え、検証の役を起動します。実測の倍率はまだありません。"
)

MicroRun = sharded.ShardedRun


def discovery_schema() -> dict[str, Any]:
    return json_schema_of(role_findings_model(_discovery_shape()))


def _discovery_shape() -> RoleShape:
    return RoleShape(
        viewpoints=VIEWPOINTS,
        require_scope_fields=True,
        allow_disclosed_at=True,
        model_written=adversarial.MODEL_WRITTEN,
    )


def lenient_discovery_model() -> type[BaseModel]:
    return role_findings_model(_discovery_shape(), lenient=True)


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
        schema=discovery_schema(),
        lenient_model=lenient_discovery_model(),
        tools=DISCOVERY_TOOLS,
        select=_select,
    )
    return sharded.run_sharded(spec, assembly, settings)


def estimate_micro(assembly: AssemblyInput, group: GroupId) -> ImplementationEstimate:
    return sharded.estimate_sharded(ImplementationEstimate, assembly, group, ESTIMATE_NOTE)
