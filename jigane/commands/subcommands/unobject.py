"""`jigane unobject`: 指摘への異議を取り下げる。"""

import argparse

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.output import print_removal, print_selection
from jigane.commands.selection_inputs import past_round_reader
from jigane.reviews import selection as selection_module
from jigane.reviews.catalog import load_review
from jigane.reviews.resolution import resolve_review


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    withdrawing = subparsers.add_parser("unobject", help="指摘への異議を取り下げます")
    withdrawing.add_argument("review")
    withdrawing.add_argument("finding")
    withdrawing.add_argument("--json", action="store_true")
    withdrawing.set_defaults(handler=run)


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    review = load_review(resolve_review(args.review))
    directory = review.id.directory()
    _, removal = selection_module.unobject(directory, args.finding, past_round_reader(directory))
    print_removal(removal)
    print_selection(
        review,
        as_json=args.json,
        heading=f"異議を外しました: {' '.join(removal.removed)}",
    )
    return EXIT_OK
