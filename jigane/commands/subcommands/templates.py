"""`jigane templates`: 返信とレポートのテンプレートを一覧する。"""

import argparse
import json

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.output import print_lines
from jigane.publishing import rendering as templates_module


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    templating = subparsers.add_parser(
        "templates", help="返信とレポートのテンプレートを一覧します"
    )
    templating.add_argument("--json", action="store_true")
    templating.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    entries = templates_module.list_templates()
    if args.json:
        print(json.dumps([e.to_dict() for e in entries], ensure_ascii=False, indent=2))
        return EXIT_OK
    print_lines([f"{e.name}\t{e.origin}\t{e.path}" for e in entries])
    return EXIT_OK
