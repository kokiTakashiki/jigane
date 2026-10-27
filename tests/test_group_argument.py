import argparse
import json
from pathlib import Path

import pytest

from jigane.commands import build_parser
from jigane.commands.subcommands.show import OVERVIEW
from jigane.groups.definitions import GROUP_IDS, GroupId
from jigane.skills.pipeline import read_runs


def _subparser(name: str) -> argparse.ArgumentParser:
    parser = build_parser()
    (action,) = (a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    subparser: argparse.ArgumentParser = action.choices[name]
    return subparser


def _positional(parser: argparse.ArgumentParser, dest: str) -> argparse.Action:
    (found,) = (a for a in parser._actions if a.dest == dest)
    return found


@pytest.mark.parametrize("command", ["run", "estimate"])
@pytest.mark.parametrize("group", list(GroupId))
def test_group_argument_is_resolved_to_group_id_at_the_entry(command: str, group: GroupId) -> None:
    arguments = build_parser().parse_args([command, "shop:p", str(group)])

    assert arguments.group is group
    assert isinstance(arguments.group, GroupId)


@pytest.mark.parametrize("command", ["run", "estimate"])
def test_group_choices_are_made_from_the_group_id_values(command: str) -> None:
    choices = _positional(_subparser(command), "group").choices
    assert choices is not None

    assert tuple(choices) == tuple(GroupId) == GROUP_IDS


@pytest.mark.parametrize("command", ["run", "estimate"])
def test_unknown_group_is_rejected_at_the_entry(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([command, "shop:p", "other"])

    error = capsys.readouterr().err
    assert "invalid choice: 'other'" in error
    assert all(group.value in error for group in GroupId)


def test_show_view_is_a_group_id_or_the_overview() -> None:
    parser = build_parser()

    assert parser.parse_args(["show", "shop:p"]).view == OVERVIEW
    assert parser.parse_args(["show", "shop:p", "overview"]).view == OVERVIEW
    for group in GroupId:
        view = parser.parse_args(["show", "shop:p", str(group)]).view
        assert view is group


def test_show_view_choices_are_made_from_the_group_id_values() -> None:
    choices = _positional(_subparser("show"), "view").choices
    assert choices is not None

    assert tuple(choices) == (OVERVIEW, *GroupId)


def test_stored_runs_are_keyed_by_group_id_when_read(tmp_path: Path) -> None:
    (tmp_path / "runs.json").write_text(json.dumps({"micro": {"discovered": 1}}), encoding="utf-8")

    (key,) = read_runs(tmp_path)

    assert key is GroupId.MICRO
