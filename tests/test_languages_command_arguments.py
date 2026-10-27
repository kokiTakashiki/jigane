from pathlib import Path

import pytest

from jigane.languages.base import CommandArgument, CommandArgumentAdapter, LanguageAdapter, parse
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.java import JavaAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter

python = PythonAdapter()


def arguments(code: str) -> list[CommandArgument]:
    source = code.encode()
    return python.command_arguments(parse(python, source).root_node, source, "cli.py")


TWO_COMMANDS = """import argparse

parser = argparse.ArgumentParser(description="x")
parser.add_argument("--verbose", action="store_true")
sub = parser.add_subparsers(dest="cmd")
draft = sub.add_parser("draft", help="d")
draft.add_argument("--region", "-r", dest="area", default="jp", required=True, help="region")
draft.add_argument("--limit", type=int, default=10)
render = sub.add_parser("render")
render.add_argument("--prefix", default=None)
render.add_argument("source", help="src")
render.add_argument("rest", nargs="*")
"""


def test_arguments_are_split_by_subcommand() -> None:
    by_command: dict[str, list[tuple[str, ...]]] = {}
    for argument in arguments(TWO_COMMANDS):
        by_command.setdefault(argument.command, []).append(argument.names)
    assert by_command == {
        "": [("--verbose",)],
        "draft": [("--region", "-r"), ("--limit",)],
        "render": [("--prefix",), ("source",), ("rest",)],
    }


def test_top_level_parser_uses_empty_command() -> None:
    found = arguments(TWO_COMMANDS)[0]
    assert (found.command, found.action) == ("", '"store_true"')


def test_dest_default_required_are_original_text() -> None:
    found = arguments(TWO_COMMANDS)
    region = found[1]
    assert region.dest == '"area"'
    assert region.default == '"jp"'
    assert region.required_text == "True"
    assert region.required is True
    assert region.help == '"region"'
    assert region.line == 7
    limit = found[2]
    assert (limit.type, limit.default, limit.dest, limit.required) == ("int", "10", None, False)


def test_positional_arguments() -> None:
    source, rest = arguments(TWO_COMMANDS)[-2:]
    assert (source.positional, source.required) == (True, True)
    assert (rest.positional, rest.required) == (True, False)


def test_default_is_not_evaluated() -> None:
    code = 'p = argparse.ArgumentParser()\np.add_argument("--a", default=os.environ["A"])\n'
    assert arguments(code)[0].default == 'os.environ["A"]'


def test_parser_received_as_function_parameter() -> None:
    code = """
def register(subparsers):
    parser = subparsers.add_parser("draft")
    add_arguments(parser)
    other = subparsers.add_parser("render")
    other.add_argument("--prefix")

def add_arguments(parser, extra=None):
    parser.add_argument("--region")
"""
    found = sorted((a.command, a.names) for a in arguments(code))
    assert found == [("draft", ("--region",)), ("render", ("--prefix",))]


def test_parser_passed_by_keyword_to_helper() -> None:
    code = """
def add(p):
    p.add_argument("--x")

def register(subparsers):
    q = subparsers.add_parser("b")
    add(p=q)
"""
    assert [a.command for a in arguments(code)] == ["b"]


def test_untraceable_forms_are_not_picked_up() -> None:
    code = """
def add_arguments(parser):
    parser.add_argument("--orphan")

def build(options):
    options.add_argument("--unknown")
    subparsers.add_parser("inline").add_argument("--chained")
    parsers[0].add_argument("--indexed")

def a(subparsers, mystery):
    add_arguments(mystery)
    add_arguments(subparsers.add_parser("x"))
"""
    assert arguments(code) == []


@pytest.mark.parametrize("adapter", [CSharpAdapter(), JavaAdapter(), SwiftAdapter()])
def test_other_languages_do_not_offer_the_reader(adapter: LanguageAdapter) -> None:
    # 未対応の言語は口を持たない。該当なし（空の一覧）と区別するため
    assert not isinstance(adapter, CommandArgumentAdapter)


GROUPS = """import argparse

parser = argparse.ArgumentParser()
sub = parser.add_subparsers()
files = sub.add_parser("files")
either = files.add_mutually_exclusive_group()
either.add_argument("--a")
either.add_argument("--b")
named = files.add_argument_group("g")
named.add_argument("--c")
files.add_argument("--d")
"""


def test_group_arguments_belong_to_the_receivers_command() -> None:
    found = [(a.command, a.names[0], a.group, a.exclusive) for a in arguments(GROUPS)]
    assert found == [
        ("files", "--a", "either", True),
        ("files", "--b", "either", True),
        ("files", "--c", "named", False),
        ("files", "--d", None, False),
    ]


def test_jigane_own_commands_pick_up_group_arguments() -> None:
    base = Path(__file__).parent.parent / "jigane/commands/subcommands"

    def grouped(name: str) -> int:
        source = (base / name).read_bytes()
        root = parse(python, source).root_node
        return sum(1 for a in python.command_arguments(root, source, name) if a.group is not None)

    assert grouped("new.py") >= 4
    assert grouped("sequence.py") >= 2
