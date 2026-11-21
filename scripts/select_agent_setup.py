"""Make／Just の setup で、未選択ならレビューエージェントを選ぶ。"""

import os
import sys

from jigane.git.process import find_executable, is_windows
from jigane.storage.agent_selection import (
    PROMPT,
    Agent,
    AgentSelectionError,
    codex_supported,
    load_agent,
    parse_agent,
    save_agent,
)


def available_agents() -> list[Agent]:
    path = os.environ.get("PATH", "")
    if is_windows():
        return [Agent.CLAUDE] if find_executable("claude.exe", path) else []
    supported = list(Agent) if codex_supported() else [Agent.CLAUDE]
    return [agent for agent in supported if find_executable(agent, path)]


def main() -> int:
    if (saved := load_agent()) is not None:
        sys.stdout.write(f"設定済みのレビューエージェントを使います: {saved}\n")
        return 0
    if "JIGANE_AGENT" in os.environ:
        agent = parse_agent(os.environ["JIGANE_AGENT"])
    elif len(available := available_agents()) == 1:
        agent = available[0]
    elif sys.stdin.isatty() and sys.stdout.isatty():
        agent = parse_agent(input(PROMPT))
    else:
        sys.stdout.write("レビューエージェントは後で jigane select-agent で選択できます\n")
        return 0
    save_agent(agent)
    sys.stdout.write(f"レビューエージェントを保存しました: {agent}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (AgentSelectionError, OSError, ValueError, EOFError) as error:
        sys.exit(f"jigane setup: {error}")
