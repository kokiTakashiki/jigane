from pathlib import Path

import pytest
from fakes import MacroFake, discovered, settings_for
from test_policy_callers import _case as callers_case
from test_policy_classes import feature_review
from test_policy_estimate import P1_CASE
from test_policy_field_copies import _range_review, _tree

from jigane.figures.models import Panel, SequenceFigure, SequenceFlow
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro_principles_run
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_principle_shapes import Shape, present_shapes
from jigane.skills.policy_principles import SHAPES_BY_PRINCIPLE, principle_prompts

NO_FLOWS = SequenceFigure(())

ABSTRACT_ONE = """from abc import ABC, abstractmethod


class Base(ABC):
    @abstractmethod
    def run(self): ...


class One(Base):
    def run(self):
        return 1
"""

ABSTRACT_TWO = ABSTRACT_ONE + "\n\nclass Two(Base):\n    def run(self):\n        return 2\n"
CONCRETE_INHERITANCE = "class Base:\n    pass\n\n\nclass Child(Base):\n    pass\n"
NO_INHERITANCE = "class Base:\n    pass\n\n\nclass Child:\n    pass\n"


def _shapes(review: Review, sequence: SequenceFigure = NO_FLOWS) -> frozenset[Shape]:
    return present_shapes(AssemblyInput.load(review), sequence)


def _numbers(review: Review) -> list[str]:
    return [item.principle.number for item in principle_prompts(AssemblyInput.load(review))]


def _before_after(case: Path) -> tuple[dict[str, str], dict[str, str]]:
    return _tree(case / "before"), _tree(case / "after")


@pytest.mark.parametrize(
    ("source", "expected"),
    [(ABSTRACT_ONE, True), (ABSTRACT_TWO, False), (CONCRETE_INHERITANCE, False)],
)
def test_single_implementation_of_an_abstract_type(
    tmp_path: Path, source: str, *, expected: bool
) -> None:
    shapes = _shapes(feature_review(tmp_path, "m.py", source))
    assert (Shape.SINGLE_IMPLEMENTATION in shapes) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [(ABSTRACT_TWO, True), (CONCRETE_INHERITANCE, True), (NO_INHERITANCE, False)],
)
def test_inheritance_edge(tmp_path: Path, source: str, *, expected: bool) -> None:
    assert (Shape.INHERITANCE in _shapes(feature_review(tmp_path, "m.py", source))) is expected


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("def f(a):\n    pass\n", "def f(a, b, c):\n    pass\n", False),
        ("def f(a):\n    pass\n", "def f(a, b, c, d):\n    pass\n", True),
        ("x = 1\n", "x = 1\n\n\ndef g(a, b):\n    pass\n", False),
        ("x = 1\n", "x = 1\n\n\ndef g(a, b, c):\n    pass\n", True),
    ],
)
def test_wide_signature(tmp_path: Path, before: str, after: str, *, expected: bool) -> None:
    shapes = _shapes(_range_review(tmp_path, {"f.py": before}, {"f.py": after}))
    assert (Shape.WIDE_SIGNATURE in shapes) is expected


def test_sequence_shape_needs_a_flow_not_a_sentence(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "m.py", "x = 1\n")
    flow = SequenceFlow("e", (Panel("p", "sequenceDiagram"),), None)
    assert Shape.SEQUENCE not in _shapes(review, NO_FLOWS)
    assert Shape.SEQUENCE in _shapes(review, SequenceFigure((flow,)))


def test_table_shapes_come_from_the_tables(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    field_copies = _range_review(tmp_path / "a", *_before_after(P1_CASE))
    assert Shape.FIELD_COPIES in _shapes(field_copies)
    (tmp_path / "b").mkdir()
    assert Shape.CALLERS in _shapes(callers_case("p2-a04", tmp_path / "b"))
    (tmp_path / "d").mkdir()
    assert _shapes(feature_review(tmp_path / "d", "m.py", "x = 1\n")) == frozenset()


def test_table_maps_each_principle_to_its_shapes() -> None:
    by_number = {p.number: shapes for p, shapes in SHAPES_BY_PRINCIPLE.items()}
    assert by_number == {
        "P1": {Shape.VALUE_SETS, Shape.FIELD_COPIES},
        "P2": {Shape.CALLERS, Shape.SINGLE_IMPLEMENTATION},
        "P3": {
            Shape.VALUE_SETS,
            Shape.DISCRIMINATORS,
            Shape.INHERITANCE,
            Shape.WIDE_SIGNATURE,
        },
        "P4": {Shape.SEQUENCE},
    }


def test_prompts_follow_the_shapes_present(tmp_path: Path) -> None:
    for name in "abc":
        (tmp_path / name).mkdir()
    assert _numbers(feature_review(tmp_path / "a", "m.py", "x = 1\n")) == []
    assert _numbers(feature_review(tmp_path / "b", "m.py", ABSTRACT_ONE)) == ["P2", "P3"]


def test_a_sequence_sentence_without_a_flow_skips_p4(tmp_path: Path) -> None:
    review = _range_review(tmp_path, *_before_after(P1_CASE))
    numbers = _numbers(review)
    assert "P1" in numbers
    assert "P4" not in numbers


def test_without_any_shape_only_the_diagram_launch_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    assembly = AssemblyInput.load(create_feature_review(root, ["src"], "p"))
    fake = MacroFake([discovered("M4")])
    result = macro_principles_run.run_policy(assembly, settings_for(fake))
    assert len(fake.calls) == 1
    assert result.run_state.launch_failures is None
    assert result.run_state.budget_stops is None
    assert macro_principles_run.estimate_policy(assembly).to_record()["planned_invocations"] == 1


def test_estimate_counts_the_assembled_prompts_plus_the_diagram_launch(
    tmp_path: Path,
) -> None:
    review = feature_review(tmp_path, "m.py", ABSTRACT_ONE)
    assembly = AssemblyInput.load(review)
    built = len(principle_prompts(assembly))
    record = macro_principles_run.estimate_policy(assembly).to_record()
    assert built == 2
    assert record["planned_invocations"] == built + 1


def test_an_interface_with_one_implementation_is_a_single_implementation(tmp_path: Path) -> None:
    source = (
        "interface Port { void send(); }\nclass Only implements Port { public void send() {} }\n"
    )
    shapes = _shapes(feature_review(tmp_path, "Only.java", source))
    assert Shape.SINGLE_IMPLEMENTATION in shapes
