"""レビューエージェントの起動口の選択を検査する。"""

import pytest

from jigane.llm_runs import claude, codex
from jigane.llm_runs.adapter import adapter_for
from jigane.llm_runs.invocation import Invoke
from jigane.storage.agent_selection import Agent


@pytest.mark.parametrize(
    ("agent", "invoke"),
    [(Agent.CLAUDE.value, claude.run), (Agent.CODEX.value, codex.run), (None, claude.run)],
)
def test_adapter_selects_runner(agent: str | None, invoke: Invoke) -> None:
    assert adapter_for(agent).invoke is invoke


def test_adapter_rejects_unsupported_agent() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        adapter_for("unsupported")
