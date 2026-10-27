import argparse
import pkgutil

from jigane.commands import build_parser, subcommands


def registered_names() -> set[str]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("副コマンドの登録が無い")


def test_modules_and_registered_subcommands_match_one_to_one() -> None:
    modules = {info.name.replace("_", "-") for info in pkgutil.iter_modules(subcommands.__path__)}
    assert registered_names() == modules


def test_no_theme_subcommand_is_registered() -> None:
    assert "theme" not in registered_names()


def test_no_post_subcommand_is_registered() -> None:
    # CLI は PR へ投稿しない。文面の書き出し（export-reply、export-report）までを受け持つ
    assert "post" not in registered_names()
    assert {"export-reply", "export-report"} <= registered_names()
    assert not {"reply", "report"} & registered_names()
