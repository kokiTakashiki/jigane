"""`jigane export-reply`: 最終的な指摘から返信の文面を作る。"""

import argparse
from pathlib import Path

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.generation import ExportKind, generate


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    reply = subparsers.add_parser(
        "export-reply", help="最終的な指摘から返信の文面を作ります（差分レビュー式）"
    )
    reply.add_argument("review")
    reply.add_argument("--template", required=True)
    reply.add_argument("--out", type=Path)
    reply.add_argument("--json", action="store_true")
    reply.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    generate(args.review, args.template, args.out, ExportKind.REPLY, as_json=args.json)
    return EXIT_OK
