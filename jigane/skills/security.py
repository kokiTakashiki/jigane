"""セキュリティのスキル。シャードごとの発見役が MCP の参照体系を引き、敵対的検証にかける。"""

from typing import Any

from pydantic import BaseModel

from jigane.findings.records import StoredFinding, triaged
from jigane.groups.definitions import SECURITY
from jigane.llm_runs import claude
from jigane.llm_runs.finding_model import RoleShape, role_findings_model
from jigane.skills import adversarial, macro, sharded
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import SecurityEstimate
from jigane.skills.mcp import NO_MCP, McpSelection
from jigane.skills.output_models import json_schema_of

GROUP = SECURITY
VIEWPOINTS = (
    "S-STORAGE",
    "S-CRYPTO",
    "S-AUTH",
    "S-NETWORK",
    "S-PLATFORM",
    "S-CODE",
    "S-RESILIENCE",
    "S-PRIVACY",
)
VIEWPOINT_PATTERN = r"^S-ASVS-V(?:[1-9]|1[0-7])$"
DISCOVERY_TOOLS = (*macro.DISCOVERY_TOOLS, *claude.MCP_RESOURCE_TOOLS)
ESTIMATE_NOTE = (
    "セキュリティはシャードごとの発見役に加え、検証の役を起動します。倍率の実測はありません。"
)


SecurityRun = sharded.ShardedRun


def discovery_schema() -> dict[str, Any]:
    return json_schema_of(role_findings_model(_discovery_shape()))


def _discovery_shape() -> RoleShape:
    return RoleShape(
        viewpoints=VIEWPOINTS,
        viewpoint_pattern=VIEWPOINT_PATTERN,
        require_references=True,
        require_scope_fields=True,
        allow_disclosed_at=True,
        model_written=adversarial.MODEL_WRITTEN,
    )


def lenient_discovery_model() -> type[BaseModel]:
    return role_findings_model(_discovery_shape(), lenient=True)


def _select(
    kept: list[StoredFinding], _last_theme_number: int, _context: sharded.SelectContext
) -> sharded.Selection:
    # テーマへ束ねる段が無く、LLM も呼ばない。区分だけを付ける。
    # 棄却でない critical と warning が必読、nit が残り。方針の要対応では保留にしない
    return sharded.Selection(tuple(triaged(SECURITY, finding) for finding in kept))


def run_security(
    assembly: AssemblyInput,
    settings: claude.RunSettings,
    mcp: McpSelection = NO_MCP,
) -> SecurityRun:
    """mcp は mcp.json で確かめ済みの選択。空なら MCP を使わない。"""
    with mcp.config_file() as mcp_config:
        spec = sharded.ShardedGroup(
            group=GROUP,
            schema=discovery_schema(),
            lenient_model=lenient_discovery_model(),
            tools=DISCOVERY_TOOLS if mcp.servers else macro.DISCOVERY_TOOLS,
            select=_select,
            mcp_config=mcp_config,
            mcp=mcp,
            allowed_mcp_tools=mcp.allowed_tools(),
        )
        return sharded.run_sharded(spec, assembly, settings)


def estimate_security(assembly: AssemblyInput, mcp: McpSelection = NO_MCP) -> SecurityEstimate:
    """mcp は実行に渡す選択。プロンプトの MCP の注記が選択で変わるため、見積もりも同じ値で組む。"""
    return sharded.estimate_sharded(SecurityEstimate, assembly, GROUP, ESTIMATE_NOTE, mcp)
