"""見積もりに数える本文のバイト数のパリティ。"""

import pytest
from agent_parity_fixtures import AgentPlan, plans

__all__ = ["plans"]


@pytest.mark.parametrize("plans", ["diagram", "design", "micro", "security"], indirect=True)
def test_estimate_body_bytes(plans: tuple[AgentPlan, AgentPlan]) -> None:
    claude, codex = plans
    # 本文や起動数の差から独立に、各見積もりの入力が本文の UTF-8 と一致することを調べる。
    if claude.estimated_bytes != claude.body_bytes:
        pytest.fail(f"Claude の見積もり {claude.estimated_bytes} B、本文 {claude.body_bytes} B")
    assert codex.estimated_bytes == codex.body_bytes
