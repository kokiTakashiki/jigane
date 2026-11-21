"""利用者が選んだレビューエージェントの保存。"""

import json
import sys
from enum import StrEnum

from jigane.storage.atomic_write import write_text_atomic
from jigane.storage.review_access import read_stored_file
from jigane.storage.store import data_home, ensure_data_home


class Agent(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


class AgentSelectionError(Exception):
    """エージェントの選択が無いか、設定を読めなかった。"""


AGENT_FILE = "agent.json"


def codex_supported() -> bool:
    return sys.platform in {"darwin", "linux"}  # noqa: TID251 Codex の対応 OS を判定する


PROMPT = "レビューエージェントを選んでください（claude: Claude Code / codex: Codex）: "


def load_agent() -> Agent | None:
    path = data_home() / AGENT_FILE
    try:
        text = read_stored_file(path.parent, path.name)
        body = json.loads(text)
        if isinstance(body, dict) and body.keys() == {"agent"}:
            return Agent(body["agent"])
    except FileNotFoundError:
        return None
    except (OSError, RecursionError, ValueError) as error:
        raise AgentSelectionError(f"エージェントの設定が不正か、読み取れません: {path}") from error
    raise AgentSelectionError(f"エージェントの設定が不正です: {path}")


def save_agent(agent: Agent) -> None:
    write_text_atomic(ensure_data_home() / AGENT_FILE, json.dumps({"agent": agent}) + "\n")


def parse_agent(value: str) -> Agent:
    try:
        return Agent(value.strip())
    except ValueError as error:
        raise AgentSelectionError("claude または codex を指定してください") from error


def require_agent() -> Agent:
    agent = load_agent()
    if agent is None:
        raise AgentSelectionError("レビューエージェントが未選択です")
    return agent
