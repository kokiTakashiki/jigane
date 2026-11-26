"""micro と security の発見計画のパリティ。"""

import pytest
from agent_parity_fixtures import AgentPlan, plans

from jigane.text.prompt_labels import MCP_AVAILABLE_LABEL

__all__ = ["plans"]

SHARDED_CASES = ("micro", "security")


def _body_without_mcp_instruction(body: str) -> str:
    return "".join(
        line
        for line in body.splitlines(keepends=True)
        if not line.startswith(MCP_AVAILABLE_LABEL.text)
    )


@pytest.mark.parametrize("plans", SHARDED_CASES, indirect=True)
def test_shard_bodies(plans: tuple[AgentPlan, AgentPlan]) -> None:
    # MCP の案内は test_security_mcp_instruction で独立に比較する。
    claude, codex = (tuple(map(_body_without_mcp_instruction, plan.bodies)) for plan in plans)
    assert claude == codex


@pytest.mark.parametrize("plans", SHARDED_CASES, indirect=True)
def test_shard_roles(plans: tuple[AgentPlan, AgentPlan]) -> None:
    claude, codex = plans
    assert claude.roles == codex.roles


@pytest.mark.parametrize("plans", SHARDED_CASES, indirect=True)
def test_shard_materials(plans: tuple[AgentPlan, AgentPlan]) -> None:
    claude, codex = (
        {line for line in plan.prompt_lines if line.startswith(("### ", "#### "))}
        for plan in plans
    )
    assert claude == codex
