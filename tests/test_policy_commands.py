from pathlib import Path

from review_fakes import make_review
from test_policy_classes import feature_review, whole

from jigane.figures.models import FigureKind, RowChange, TableFigure
from jigane.skills import macro, policy_commands
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import REGISTRY, diagrams_of
from jigane.storage.store import ReviewKind

CLI = """import argparse

parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="cmd")
draft = sub.add_parser("draft")
draft.add_argument("--region", default="jp", required=True)
render = sub.add_parser("render")
render.add_argument("--prefix", default="s")
either = render.add_mutually_exclusive_group()
either.add_argument("--a", action="store_true")
"""


def test_table_has_one_row_per_argument_ordered_by_file_and_command(tmp_path: Path) -> None:
    figure = policy_commands.command_table(feature_review(tmp_path, "cli.py", CLI))
    assert isinstance(figure, TableFigure)
    assert figure.kind == FigureKind.TABLE
    assert figure.header[:3] == ("ファイル", "副コマンド", "引数")
    assert [(r.cells[1], r.cells[2]) for r in figure.rows] == [
        ("draft", "--region"),
        ("render", "--prefix"),
        ("render", "--a（排他 either）"),
    ]
    assert figure.rows[0].cells[4:6] == ('"jp"', "はい")
    text = figure.text()
    assert text.startswith(policy_commands.INTRO)
    assert "| cli.py | draft | --region |" in text


def test_diff_review_marks_added_removed_and_changed_default(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    head = "import argparse\np = argparse.ArgumentParser()\n"
    before_source = (
        head + 'p.add_argument("--keep", default="1")\n'
        'p.add_argument("--same")\np.add_argument("--gone")\n'
    )
    after_source = (
        head + 'p.add_argument("--keep", default="2")\n'
        'p.add_argument("--same")\np.add_argument("--new")\n'
    )
    (base / "cli.py").write_text(before_source, encoding="utf-8")
    (after / "cli.py").write_text(after_source, encoding="utf-8")
    review = make_review(
        after,
        ["cli.py"],
        base_tree=base,
        judgment_map={"segments": [whole("cli.py", 5)], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    figure = policy_commands.command_table(review)
    assert figure is not None
    marks = {row.cells[2]: row.change for row in figure.rows}
    assert marks == {
        "~--keep": RowChange.CHANGED,
        "--same": RowChange.UNCHANGED,
        "+--new": RowChange.ADDED,
        "---gone": RowChange.REMOVED,
    }


def test_no_figure_without_arguments(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "m.py", "x = 1\n")
    assert policy_commands.command_table(review) is None
    assert "commands" not in [p.key for p in diagrams_of(review).figures]


def test_registered_after_tree_and_reaches_prompt_and_diagrams(tmp_path: Path) -> None:
    entry = next(e for e in REGISTRY if e.key == "commands")
    assert entry.slot == "after_tree"
    review = feature_review(tmp_path, "cli.py", CLI)
    placed = next(p for p in diagrams_of(review).figures if p.key == "commands")
    assert placed.to_json()["kind"] == "table"
    assert "--region" in macro.diagram_materials_text(
        AssemblyInput.load(review), macro.policy_sections(review)
    )
