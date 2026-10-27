"""シャードごとの発見役のプロンプトを、本体の組み立てで作る試験の補助。"""

from jigane.groups.definitions import GroupId
from jigane.skills import sharded
from jigane.skills.assembly import AssemblyInput
from jigane.skills.mcp import NO_MCP, McpSelection
from jigane.skills.shards import Shard


def shard_prompt(
    group: GroupId,
    assembly: AssemblyInput,
    shard: Shard,
    number: int,
    total: int,
    mcp: McpSelection = NO_MCP,
) -> str:
    return sharded.build_shard_prompt(
        group,
        assembly,
        shard,
        number,
        total,
        concerns=sharded.shard_concerns(group, assembly.review, shard),
        mcp=mcp,
    )
