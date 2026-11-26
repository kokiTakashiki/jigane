"""発見役に渡す MCP の指定と案内のパリティ。"""

import pytest
from agent_parity_fixtures import MCP_GUIDE, MCP_NAMES, AgentPlan, plans

from jigane.text.prompt_labels import MCP_AVAILABLE_LABEL

__all__ = ["plans"]


@pytest.mark.parametrize(
    ("plans", "expected"),
    [("diagram", ()), ("design", ()), ("micro", ()), ("security", MCP_NAMES)],
    indirect=["plans"],
)
def test_selected_mcp(plans: tuple[AgentPlan, AgentPlan], expected: tuple[str, ...]) -> None:
    claude, codex = plans
    assert {call.mcp_server_names for call in claude.calls} == {expected}
    assert {tuple(server.name for server in call.reference_mcp) for call in codex.calls} == {
        expected
    }
    for plan in plans:
        for body in plan.bodies:
            for name, use in zip(expected, MCP_GUIDE[: len(expected)], strict=True):
                assert f"- {name}: {use}" in body


@pytest.mark.parametrize("plans", ["security"], indirect=True)
def test_security_mcp_instruction(plans: tuple[AgentPlan, AgentPlan]) -> None:
    claude, codex = (
        {line for line in plan.prompt_lines if line.startswith(MCP_AVAILABLE_LABEL.text)}
        for plan in plans
    )
    if not claude or not codex:
        pytest.fail("選んだ MCP の案内が発見役の本文にありません")
    assert claude == codex
