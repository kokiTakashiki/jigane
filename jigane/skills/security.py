"""セキュリティのスキル。シャードごとの発見役が MCP の参照体系を引き、敵対的検証にかける。"""

from jigane.findings.records import StoredFinding, triaged
from jigane.groups import viewpoints
from jigane.groups.definitions import SECURITY
from jigane.llm_runs import claude
from jigane.skills import macro, sharded
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import SecurityEstimate
from jigane.skills.mcp import NO_MCP, McpSelection

GROUP = SECURITY
# 問いの文を持つ観点だけ。プロンプトの一覧に使う。スキーマが受ける観点は群の表の全行
VIEWPOINTS = tuple(
    viewpoint.number for viewpoint in viewpoints.of_group(SECURITY) if viewpoint.question
)
DISCOVERY = sharded.discovery_spec(GROUP, require_references=True)
DISCOVERY_TOOLS = (*macro.DISCOVERY_TOOLS, *claude.MCP_RESOURCE_TOOLS)
ESTIMATE_NOTE = (
    "セキュリティはシャードごとの発見役に加え、検証の役を起動します。倍率の実測はありません。"
)


SecurityRun = sharded.ShardedRun


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
            spec=DISCOVERY,
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
