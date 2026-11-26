"""図と設計書の macro のパリティ。"""

import pytest
from agent_parity_fixtures import AgentPlan, plans

__all__ = ["plans"]


@pytest.mark.parametrize("plans", ["diagram", "design"], indirect=True)
def test_macro_main_body(plans: tuple[AgentPlan, AgentPlan]) -> None:
    claude, codex = plans
    assert claude.bodies[0] == codex.bodies[0]


@pytest.mark.parametrize("plans", ["diagram"], indirect=True)
def test_macro_principle_bodies(plans: tuple[AgentPlan, AgentPlan]) -> None:
    claude, codex = (
        {call.principle: call.prompt for call in plan.calls if call.principle} for plan in plans
    )
    shared = claude.keys() & codex.keys()
    if not shared:
        pytest.fail("共通の原則の起動がありません")
    assert {key: claude[key] for key in shared} == {key: codex[key] for key in shared}


@pytest.mark.parametrize("plans", ["diagram", "design"], indirect=True)
def test_macro_roles(plans: tuple[AgentPlan, AgentPlan]) -> None:
    claude, codex = plans
    assert claude.roles == codex.roles


@pytest.mark.parametrize("plans", ["diagram", "design"], indirect=True)
def test_macro_materials(plans: tuple[AgentPlan, AgentPlan]) -> None:
    claude, codex = (
        {line for line in plan.bodies[0].splitlines() if line.startswith(("### ", "#### "))}
        for plan in plans
    )
    assert claude == codex
