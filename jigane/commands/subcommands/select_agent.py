"""レビューエージェントの選択と確認。"""

import argparse
import json

from jigane.commands.context import Context
from jigane.storage.agent_selection import (
    PROMPT,
    Agent,
    AgentSelectionError,
    load_agent,
    parse_agent,
    save_agent,
)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = subparsers.add_parser("select-agent", help="レビューエージェントを選択・確認します")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("agent", nargs="?", type=Agent, choices=list(Agent))
    mode.add_argument("--show", action="store_true", help="保存された選択を表示します")
    parser.add_argument("--json", action="store_true", help="--show の結果を JSON で返します")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    if args.show:
        agent = load_agent()
        print(json.dumps({"agent": agent}) if args.json else agent or "未選択")
        return 0
    if args.json:
        raise ValueError("--json は --show と一緒に指定してください")
    agent = args.agent
    if agent is None:
        if not context.is_terminal():
            raise AgentSelectionError("端末で選択するか、エージェント名を指定してください")
        agent = parse_agent(context.read_answer(PROMPT))
    save_agent(agent)
    print(f"レビューエージェントを保存しました: {agent}")
    return 0
