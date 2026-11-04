from pathlib import Path

import pytest
from review_fakes import make_review
from test_policy_classes import whole

from jigane.figures.models import TableFigure
from jigane.reviews.lifecycle import Review
from jigane.skills import policy_field_copies
from jigane.skills.policy_figures import (
    LANGUAGE_AGNOSTIC,
    REGISTRY,
    FigureSlot,
    diagrams_of,
)
from jigane.storage.store import ReviewKind

CASE = Path(__file__).parent / "fixtures" / "policy_material" / "field_copies" / "p1-a12"
# 実験の例題 p1-a12 の照合。表の同じ一行に、これらの語がすべて載ること
IDENTIFIER_GROUPS = (["BookRow", "BookDto", "isbn"], ["BookRow", "Book", "pages"])


def _write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")


def _range_review(tmp_path: Path, before: dict[str, str], after: dict[str, str]) -> Review:
    base, head = tmp_path / "base", tmp_path / "after"
    base.mkdir()
    head.mkdir()
    _write(base, before)
    _write(head, after)
    changed = sorted(path for path in after if before.get(path) != after[path])
    return make_review(
        head,
        changed,
        base_tree=base,
        judgment_map={
            "segments": [whole(path, 99) for path in changed],
            "regions": [],
            "line_counts": {},
        },
        kind=ReviewKind.RANGE,
    )


def _tree(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix().removesuffix(".txt"): p.read_text(encoding="utf-8")
        for p in sorted(directory.rglob("*.py.txt"))
    }


def _table(review: Review) -> TableFigure | None:
    return policy_field_copies.field_copy_table(review)


def test_fixture_case_lines_carry_the_expected_identifiers(tmp_path: Path) -> None:
    figure = _table(_range_review(tmp_path, _tree(CASE / "before"), _tree(CASE / "after")))
    assert figure is not None
    lines = figure.text().splitlines()
    for group in IDENTIFIER_GROUPS:
        assert any(all(word in line for word in group) for line in lines), group
    assert figure.header == policy_field_copies.HEADER
    assert figure.text().startswith(policy_field_copies.INTRO)


def test_added_type_lists_each_type_sharing_two_or_more_fields(tmp_path: Path) -> None:
    figure = _table(_range_review(tmp_path, _tree(CASE / "before"), _tree(CASE / "after")))
    assert figure is not None
    assert [row.cells for row in figure.rows] == [
        (
            "`BookRow`",
            "importer.py:5",
            "`BookDto`",
            "api.py:5",
            "`isbn`、`title`、`author`、`pages`",
            "4",
        ),
        (
            "`BookRow`",
            "importer.py:5",
            "`Book`",
            "catalog.py:5",
            "`isbn`、`title`、`author`、`pages`",
            "4",
        ),
    ]


def test_type_whose_fields_changed_is_listed(tmp_path: Path) -> None:
    before = {
        "a.py": "class A:\n    x: int\n    y: int\n",
        "b.py": "class B:\n    x: int\n    y: int\n    z: int\n",
    }
    after = {**before, "a.py": "class A:\n    x: int\n    y: int\n    z: int\n"}
    figure = _table(_range_review(tmp_path, before, after))
    assert figure is not None
    assert [(row.cells[0], row.cells[2], row.cells[5]) for row in figure.rows] == [
        ("`A`", "`B`", "3")
    ]


def test_unchanged_type_is_not_listed(tmp_path: Path) -> None:
    files = {
        "a.py": "class A:\n    x: int\n    y: int\n",
        "b.py": "class B:\n    x: int\n    y: int\n",
        "c.py": "v = 1\n",
    }
    after = {**files, "c.py": "v = 2\n"}
    assert _table(_range_review(tmp_path, files, after)) is None


def test_sharing_a_single_field_is_not_listed(tmp_path: Path) -> None:
    before = {"b.py": "class B:\n    x: int\n    y: int\n"}
    after = {**before, "a.py": "class A:\n    x: int\n    w: int\n"}
    assert _table(_range_review(tmp_path, before, after)) is None


def test_names_starting_with_underscore_are_excluded(tmp_path: Path) -> None:
    before = {"b.py": "class B:\n    _x: int\n    _y: int\n    z: int\n"}
    after = {**before, "a.py": "class A:\n    _x: int\n    _y: int\n    z: int\n"}
    assert _table(_range_review(tmp_path, before, after)) is None


def test_enum_values_count_as_fields(tmp_path: Path) -> None:
    enum = "from enum import Enum\n\n\nclass {name}(Enum):\n    LOW = 1\n    HIGH = 2\n"
    before = {"b.py": enum.format(name="B")}
    after = {**before, "a.py": enum.format(name="A")}
    figure = _table(_range_review(tmp_path, before, after))
    assert figure is not None
    assert figure.rows[0].cells[4] == "`LOW`、`HIGH`"


def test_plain_class_assignments_without_annotation_are_not_fields(tmp_path: Path) -> None:
    before = {"b.py": "class B:\n    x = 1\n    y = 2\n"}
    after = {**before, "a.py": "class A:\n    x = 1\n    y = 2\n"}
    assert _table(_range_review(tmp_path, before, after)) is None


def test_only_python_reads_type_fields(tmp_path: Path) -> None:
    java = "class A { int x; int y; }\n"
    review = _range_review(tmp_path, {"B.java": java}, {"B.java": java, "A.java": java})
    assert _table(review) is None


def test_python_rows_without_unsupported_note_when_languages_are_mixed(tmp_path: Path) -> None:
    java = "class A { int x; int y; }\n"
    before = {"b.py": "class B:\n    x: int\n    y: int\n"}
    after = {**before, "a.py": "class A:\n    x: int\n    y: int\n", "J.java": java}
    review = _range_review(tmp_path, before, after)
    figure = _table(review)
    assert figure is not None
    assert [row.cells[0] for row in figure.rows] == ["`A`"]
    assert all(u.key != "field_copies" for u in diagrams_of(review).unsupported)


def test_file_review_has_no_table(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("class A:\n    x: int\n    y: int\n", encoding="utf-8")
    review = make_review(
        tmp_path,
        ["a.py"],
        judgment_map={"segments": [whole("a.py", 3)], "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )
    assert _table(review) is None


def test_registered_in_after_regions_as_language_agnostic(tmp_path: Path) -> None:
    entry = next(e for e in REGISTRY if e.key == "field_copies")
    assert entry.slot == FigureSlot.AFTER_REGIONS
    assert entry.interface is LANGUAGE_AGNOSTIC
    review = _range_review(tmp_path, _tree(CASE / "before"), _tree(CASE / "after"))
    placed = next(p for p in diagrams_of(review).figures if p.key == "field_copies")
    assert placed.to_json()["kind"] == "table"


@pytest.mark.parametrize("terms", [600, 20_000])
def test_file_with_a_deep_expression_is_skipped_and_others_remain(
    tmp_path: Path, terms: int
) -> None:
    fields = "    isbn: str\n    title: str\n"
    deep = "x = " + "+".join(["a"] * terms) + "\n"
    figure = _table(
        _range_review(
            tmp_path,
            {},
            {"deep.py": deep, "a.py": f"class A:\n{fields}", "b.py": f"class B:\n{fields}"},
        )
    )
    assert figure is not None
    assert [row.cells[0] for row in figure.rows] == ["`A`", "`B`"]
