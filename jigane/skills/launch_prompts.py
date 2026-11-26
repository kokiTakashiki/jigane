"""群から、起動ごとのプロンプトの本文と見積もりへの対応。

群で見積もりとプロンプトを分ける分岐はこの match だけに置く。

`estimator.estimate_of` が数える起動の数は、ここが返す件数と一致する。
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from jigane.groups.definitions import GroupId
from jigane.groups.policy_runs import PolicyRole, role_of
from jigane.llm_runs import claude, codex
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
    text: str
    policy_role: PolicyRole | None = None
    principle: str | None = None
    discovery_group: GroupId | None = None

    @property
    def role(self) -> LaunchRole:
        return (
            LaunchRole.PRINCIPLE
            if self.policy_role is PolicyRole.PRINCIPLE
            else LaunchRole.DISCOVERY
        )

    @property
    def size(self) -> int:
        """本文の UTF-8 のバイト数。"""
        return len(self.text.encode("utf-8"))

    def codex_model(self) -> codex.Model:
        return codex.model_for(
            self.policy_role, self.principle, discovery_group=self.discovery_group
        )


@dataclass(frozen=True)
class LaunchInput:
    """起動の組み立てに要る入力。

    mcp はセキュリティの群の MCP の注記を変える。budget は上限に収まるまでシャードを増やすので、
    起動の数と分け方を変える。差分シャード資料の縮約は budget の compact_materials で選ぶ。
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
        return tuple(LaunchPrompt(text, discovery_group=group) for text in planned.prompts)

    def policy_prompts() -> tuple[LaunchPrompt, ...]:
        return (
            LaunchPrompt(
                macro.remembered_prompt(assembly),
                role_of(macro.policy_shape(assembly.review)),
            ),
            *(
                LaunchPrompt(launch.prompt, PolicyRole.PRINCIPLE, launch.viewpoint)
                for launch in macro_principles_run.principle_launches(assembly)
            ),
        )

    match group:
        case GroupId.MACRO:
            return GroupLaunch(
                prompts=policy_prompts,
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
