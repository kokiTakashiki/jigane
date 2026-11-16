"""群から、起動ごとのプロンプトの本文と見積もりへの対応。

群で見積もりとプロンプトを分ける分岐はこの match だけに置く。

`estimator.estimate_of` が数える起動の数は、ここが返す件数と一致する。
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from jigane.groups.definitions import GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.launch_failure import DISCOVERY_ROLE
from jigane.skills import macro, macro_principles_run, micro, security, sharded
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import Estimate
from jigane.skills.mcp import NO_MCP, McpSelection


class LaunchRole(StrEnum):
    """起動の種類。"""

    DISCOVERY = DISCOVERY_ROLE
    PRINCIPLE = "principle"


@dataclass(frozen=True)
class LaunchPrompt:
    role: LaunchRole
    text: str

    @property
    def size(self) -> int:
        """本文の UTF-8 のバイト数。"""
        return len(self.text.encode("utf-8"))


@dataclass(frozen=True)
class LaunchInput:
    """起動の組み立てに要る入力。

    mcp はセキュリティの群の MCP の注記を変える。budget は上限に収まるまでシャードを増やすので、
    起動の数と分け方を変える。方針の群はどちらにも影響されない。
    """

    assembly: AssemblyInput
    group: GroupId
    mcp: McpSelection = NO_MCP
    budget: claude.RunBudget | None = None


@dataclass(frozen=True)
class GroupLaunch:
    """その群の起動の組み立て。

    見積もりだけが要るときにプロンプトを組まず、その逆も避けるため、二つの Callable で遅延させる。
    """

    prompts: Callable[[], tuple[LaunchPrompt, ...]]
    estimate: Callable[[], Estimate]


def group_launch(launch_input: LaunchInput) -> GroupLaunch:
    """群で見積もりとプロンプトを分ける分岐の唯一の置き場。"""
    assembly, group = launch_input.assembly, launch_input.group
    mcp, budget = launch_input.mcp, launch_input.budget

    def shard_prompts() -> tuple[LaunchPrompt, ...]:
        planned = sharded.plan_shards(assembly, group, mcp, budget)
        return tuple(LaunchPrompt(LaunchRole.DISCOVERY, text) for text in planned.prompts)

    match group:
        case GroupId.MACRO:
            return GroupLaunch(
                prompts=lambda: (
                    LaunchPrompt(LaunchRole.DISCOVERY, macro.remembered_prompt(assembly)),
                    *(
                        LaunchPrompt(LaunchRole.PRINCIPLE, launch.prompt)
                        for launch in macro_principles_run.principle_launches(assembly)
                    ),
                ),
                # 差し替えが効くよう、呼ぶ時点で属性を引く
                estimate=lambda: macro_principles_run.estimate_policy(assembly),
            )
        case GroupId.MICRO:
            return GroupLaunch(
                prompts=shard_prompts,
                estimate=lambda: micro.estimate_micro(assembly, group, budget),
            )
        case GroupId.SECURITY:
            return GroupLaunch(
                prompts=shard_prompts,
                estimate=lambda: security.estimate_security(assembly, mcp, budget),
            )
        case _:
            assert_never(group)


def launch_prompts(launch_input: LaunchInput) -> tuple[LaunchPrompt, ...]:
    """その群が起動ごとに渡すプロンプトの本文。"""
    return group_launch(launch_input).prompts()
