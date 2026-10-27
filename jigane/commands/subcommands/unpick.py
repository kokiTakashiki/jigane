"""`jigane unpick`: 指摘（F、B の番号）を最終的な指摘から外す"""

import argparse

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.output import print_removal, print_selection
from jigane.reviews import selection as selection_module
from jigane.reviews.lifecycle import load_review, resolve_review


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    selecting = subparsers.add_parser(
        "unpick", help="指摘（F、B の番号）を最終的な指摘から外します"
    )
    selecting.add_argument("review")
    selecting.add_argument("findings", nargs="+", metavar="finding")
    selecting.add_argument("--json", action="store_true")
    selecting.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    review = load_review(resolve_review(args.review))
    _, removal = selection_module.unpick(review.id.directory(), args.findings)
    print_removal(removal)
    print_selection(review, as_json=args.json)
    return EXIT_OK
