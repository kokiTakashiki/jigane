"""`jigane unarchive`: アーカイブしたレビューを一覧へ戻す。"""

import argparse

from jigane.commands.context import Context
from jigane.commands.subcommands.archive import change_archive


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    unarchive = subparsers.add_parser("unarchive", help="アーカイブしたレビューを一覧へ戻します")
    unarchive.add_argument("review")
    unarchive.add_argument("--json", action="store_true")
    unarchive.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 印を付け外しするだけで LLM も外部のコマンドも呼ばない
    return change_archive(args.review, archived=False, as_json=args.json)
