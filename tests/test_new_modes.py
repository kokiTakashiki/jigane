import argparse

from jigane.commands import build_parser
from jigane.commands.subcommands.new import NewMode
from jigane.storage.store import ReviewKind


def test_new_modes_match_the_parser_choices() -> None:
    parser = build_parser()
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    new_parser = top.choices["new"]
    modes = next(a for a in new_parser._actions if isinstance(a, argparse._SubParsersAction))
    assert set(modes.choices) == {mode.value for mode in NewMode}


def test_every_review_kind_is_created_by_exactly_one_new_mode() -> None:
    assert sorted(mode.review_kind for mode in NewMode) == sorted(ReviewKind)
