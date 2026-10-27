"""群から見積もりの型への対応。群ごとの分岐はこの match だけに置く。"""

from typing import assert_never

from jigane.groups.definitions import GroupId
from jigane.skills import macro, micro, security
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import Estimate
from jigane.skills.mcp import NO_MCP, McpSelection


def estimate_of(assembly: AssemblyInput, group: GroupId, mcp: McpSelection = NO_MCP) -> Estimate:
    match group:
        case GroupId.MACRO:
            return macro.estimate_macro(assembly)
        case GroupId.MICRO:
            return micro.estimate_micro(assembly, group)
        case GroupId.SECURITY:
            return security.estimate_security(assembly, mcp)
        case _:
            assert_never(group)
