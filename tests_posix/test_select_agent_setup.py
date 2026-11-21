import sys
from pathlib import Path

import pytest
import select_agent_setup

from jigane.storage.agent_selection import Agent, load_agent, save_agent


@pytest.fixture
def agent_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("JIGANE_AGENT", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    return tmp_path


def executable(directory: Path, name: Agent) -> None:
    path = directory / name
    path.touch()
    path.chmod(0o700)


@pytest.mark.parametrize("agent", list(Agent))
def test_setup_saves_the_one_path_candidate(
    agent_path: Path, agent: Agent, capsys: pytest.CaptureFixture[str]
) -> None:
    executable(agent_path, agent)
    assert select_agent_setup.main() == 0
    assert load_agent() == agent
    assert agent in capsys.readouterr().out


@pytest.mark.parametrize("agents", [[], list(Agent)])
def test_noninteractive_setup_defers_ambiguous_selection(
    agent_path: Path, agents: list[Agent], capsys: pytest.CaptureFixture[str]
) -> None:
    for agent in agents:
        executable(agent_path, agent)
    assert select_agent_setup.main() == 0
    assert load_agent() is None
    assert "jigane select-agent" in capsys.readouterr().out


def test_setup_asks_in_terminal_when_both_agents_exist(
    agent_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for agent in Agent:
        executable(agent_path, agent)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    prompts = []

    def choose(prompt: str) -> str:
        prompts.append(prompt)
        return "codex"

    monkeypatch.setattr("builtins.input", choose)
    assert select_agent_setup.main() == 0
    assert load_agent() == Agent.CODEX
    assert len(prompts) == 1


def test_setup_environment_selection_overrides_path(
    agent_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable(agent_path, Agent.CLAUDE)
    monkeypatch.setenv("JIGANE_AGENT", "codex")
    assert select_agent_setup.main() == 0
    assert load_agent() == Agent.CODEX


def test_setup_keeps_saved_selection(agent_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable(agent_path, Agent.CODEX)
    save_agent(Agent.CLAUDE)
    monkeypatch.setenv("JIGANE_AGENT", "codex")
    assert select_agent_setup.main() == 0
    assert load_agent() == Agent.CLAUDE
