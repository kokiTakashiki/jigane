"""`jigane object`: 指摘への異議を記録する。"""

import argparse

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.output import print_lost_reasons, print_selection
from jigane.reviews import selection as selection_module
from jigane.reviews.lifecycle import load_review, resolve_review


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    objection = subparsers.add_parser("object", help="指摘への異議を記録します")
    objection.add_argument("review")
    objection.add_argument("finding")
    objection.add_argument("--reason", required=True)
    objection.add_argument("--json", action="store_true")
    objection.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    review = load_review(resolve_review(args.review))
    _, lost = selection_module.object_to(review.id.directory(), args.finding, args.reason)
    print_lost_reasons(lost)
    print_selection(review, as_json=args.json)
    return EXIT_OK
