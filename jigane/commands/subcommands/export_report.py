"""`jigane export-report`: 最終的な指摘からレポートを書き出す。"""

import argparse
from pathlib import Path

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.generation import ExportKind, generate


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    report = subparsers.add_parser("export-report", help="最終的な指摘からレポートを書き出します")
    report.add_argument("review")
    report.add_argument("--template", required=True)
    report.add_argument("--out", type=Path)
    report.add_argument("--json", action="store_true")
    report.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    generate(args.review, args.template, args.out, ExportKind.REPORT, as_json=args.json)
    return EXIT_OK
