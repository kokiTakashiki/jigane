from pathlib import Path

from review_fakes import make_review
from test_policy_classes import feature_review, whole

from jigane.figures.models import FigureKind, RowChange, TableFigure
from jigane.skills import macro, policy_file_paths
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import REGISTRY, diagrams_of
from jigane.storage.store import ReviewKind

SOURCE = """def render(out, prefix, index):
    target = out / f"{prefix}_{index:03d}.png"
    target.write_bytes(b"")
    open("table.csv").read()
"""


def test_table_lists_file_function_usage_and_expression(tmp_path: Path) -> None:
    figure = policy_file_paths.file_path_table(feature_review(tmp_path, "r.py", SOURCE))
    assert isinstance(figure, TableFigure)
    assert figure.kind == FigureKind.TABLE
    assert figure.header == ("ファイル", "関数", "使い道", "組み立ての原文")
    assert tuple(row.cells for row in figure.rows) == (
        ("r.py", "render", "書く", 'out / f"{prefix}_{index:03d}.png"'),
        ("r.py", "render", "読む", '"table.csv"'),
    )
    assert figure.text().startswith(policy_file_paths.INTRO)


def test_diff_review_marks_added_removed_and_changed_usage(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    (base / "r.py").write_text(
        'open("keep.txt")\nopen("gone.txt", "w")\nopen("flip.txt")\n', encoding="utf-8"
    )
    (after / "r.py").write_text(
        'open("keep.txt")\nopen("new.txt", "w")\nopen("flip.txt", "w")\n', encoding="utf-8"
    )
    review = make_review(
        after,
        ["r.py"],
        base_tree=base,
        judgment_map={"segments": [whole("r.py", 3)], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    figure = policy_file_paths.file_path_table(review)
    assert figure is not None
    marks = {row.cells[3]: row.change for row in figure.rows}
    assert marks == {
        '"keep.txt"': RowChange.UNCHANGED,
        '"new.txt"': RowChange.ADDED,
        '"flip.txt"': RowChange.CHANGED,
        '"gone.txt"': RowChange.REMOVED,
    }


def test_test_files_are_left_out(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    review = feature_review(tmp_path, "tests/test_r.py", SOURCE)
    assert policy_file_paths.file_path_table(review) is None


def test_no_figure_without_file_paths(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "m.py", "x = 1\n")
    assert policy_file_paths.file_path_table(review) is None
    assert "file_paths" not in [p.key for p in diagrams_of(review).figures]


def test_registered_and_reaches_prompt_and_diagrams(tmp_path: Path) -> None:
    assert any(e.key == "file_paths" for e in REGISTRY)
    review = feature_review(tmp_path, "r.py", SOURCE)
    placed = next(p for p in diagrams_of(review).figures if p.key == "file_paths")
    assert placed.to_json()["kind"] == "table"
    text = macro.diagram_materials_text(AssemblyInput.load(review), macro.policy_sections(review))
    assert "{prefix}_{index:03d}" in text


def test_same_expression_read_then_write_is_not_marked_changed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    after = tmp_path / "after"
    base.mkdir()
    after.mkdir()
    body = (
        "def run(root, name):\n"
        '    path = Path(f"{root}/{name}")\n'
        "    content = path.read_text()\n"
        "    path.write_text(content)\n"
    )
    (base / "r.py").write_text(body, encoding="utf-8")
    (after / "r.py").write_text(body, encoding="utf-8")
    review = make_review(
        after,
        ["r.py"],
        base_tree=base,
        judgment_map={"segments": [whole("r.py", 4)], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    figure = policy_file_paths.file_path_table(review)
    assert figure is not None
    assert {row.change for row in figure.rows} == {RowChange.UNCHANGED}
