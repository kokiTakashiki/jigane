import dataclasses
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from review_fakes import changed_segment

from jigane.languages.csharp import CSharpAdapter
from jigane.sequence.diagram import (
    ArrowHit,
    Diagram,
    DiagramInputs,
    DiagramSet,
    DiagramSetNotes,
    ParticipantHit,
    UnreachableRegion,
    construct_diagrams,
)
from jigane.sequence.diagram_json import diagram_to_dict, diagrams_to_dict
from jigane.sequence.identifiers import IdentifiedDiagram, identities_of
from jigane.sequence.index import Index, build_index
from jigane.sequence.model_json import model_to_json
from jigane.sequence.review_diagrams import build_diagrams
from jigane.sequence.roots import RootSelection, changed_members, select_roots
from jigane.sequence.side import Side, TreeSide

if TYPE_CHECKING:
    from jigane.judgment_map.classification import Segment

SOURCE = """namespace Demo;

public static class Format
{
    public static void Line() { }
    public static void Rule() { }
}

public class Service
{
    public void Run(int count)
    {
        Format.Line();
        Format.Rule();
        Format.Line();
        Format.Line();
    }

    public void Run(string text)
    {
        Format.Line();
    }
}
"""


def _single(diagram: Diagram) -> DiagramSet:
    return DiagramSet((diagram,), DiagramSetNotes({}, (), ()))


def _index(directory: Path, source: str = SOURCE) -> Index:
    (directory / "App.cs").write_text(source, encoding="utf-8")
    return build_index(directory, ["App.cs"], CSharpAdapter())


def _selection(index: Index, name: str = "Service.Run") -> RootSelection:
    segments: list[Segment] = []
    for member in index.callables.values():
        declaration = member.declaration
        if member.qualified_name == name and declaration is not None:
            line = declaration.span.start_line
            segments.append(changed_segment("App.cs", line, line, name))
    changed = changed_members(index, segments, CSharpAdapter(), include_tests=False)
    return select_roots(index, changed, CSharpAdapter())


def _model(index: Index, side: TreeSide = Side.AFTER) -> dict[str, Any]:
    model = build_diagrams(DiagramInputs(index, _selection(index), [], 4, full=True), side)
    return model_to_json(model)


def _by_arity(model: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {len(d["arrows"]): d for d in model["diagrams"]}


def test_rebuilding_a_diagram_gives_the_same_identifiers(tmp_path: Path) -> None:
    first = _model(_index(tmp_path))
    second = _model(_index(tmp_path))
    assert first["diagrams"] == second["diagrams"]
    assert all(d["id"] and all(a["id"] for a in d["arrows"]) for d in first["diagrams"])


def test_overloaded_roots_get_different_diagram_identifiers(tmp_path: Path) -> None:
    model = _model(_index(tmp_path))
    assert [d["root"] for d in model["diagrams"]] == ["Service.Run", "Service.Run"]
    first, second = model["diagrams"]
    assert first["id"] != second["id"]
    assert {a["id"] for a in first["arrows"]}.isdisjoint(a["id"] for a in second["arrows"])


def test_identical_arrows_in_one_diagram_are_told_apart_by_occurrence(tmp_path: Path) -> None:
    diagram = _by_arity(_model(_index(tmp_path)))[4]
    line_arrows = [a for a in diagram["arrows"] if a["name"] == "Line"]
    assert len(line_arrows) == 3
    assert len({a["id"] for a in diagram["arrows"]}) == 4


def test_identifier_follows_the_content_not_the_position(tmp_path: Path) -> None:
    before = _by_arity(_model(_index(tmp_path)))[4]
    swapped = SOURCE.replace(
        "        Format.Line();\n        Format.Rule();\n",
        "        Format.Rule();\n        Format.Line();\n",
    )
    after = _by_arity(_model(_index(tmp_path, swapped)))[4]
    assert [a["name"] for a in before["arrows"]] != [a["name"] for a in after["arrows"]]
    # 行が入れ替わるので、同じ行に同じ呼び出しがある矢印だけが同じ識別子を保つ
    ids_before = {(a["name"], a["line"]): a["id"] for a in before["arrows"]}
    ids_after = {(a["name"], a["line"]): a["id"] for a in after["arrows"]}
    assert ids_before[("Line", 15)] == ids_after[("Line", 15)]
    assert ids_before[("Line", 16)] == ids_after[("Line", 16)]
    assert before["id"] == after["id"]


def test_reordering_arrows_keeps_each_identifier(tmp_path: Path) -> None:
    index = _index(tmp_path)
    built = construct_diagrams(DiagramInputs(index, _selection(index), [], 4, full=True))
    diagram = next(d for d in built.diagrams if len(d.arrows) == 4)
    reordered = dataclasses.replace(diagram, arrows=list(reversed(diagram.arrows)))
    original_ids = identities_of(_single(diagram), Side.AFTER)[0].identities
    reordered_ids = identities_of(_single(reordered), Side.AFTER)[0].identities
    assert reordered_ids.diagram_id == original_ids.diagram_id
    assert {(i.arrow.line, i.arrow.name, i.arrow_id) for i in reordered_ids.arrows} == {
        (i.arrow.line, i.arrow.name, i.arrow_id) for i in original_ids.arrows
    }


def test_identical_calls_on_one_line_are_told_apart_by_occurrence(tmp_path: Path) -> None:
    source = SOURCE.replace(
        "    public void Run(string text)\n    {\n        Format.Line();\n    }\n",
        "    public void Twice()\n    {\n        Format.Line(); Format.Line();\n    }\n",
    )
    index = _index(tmp_path, source)
    model = model_to_json(
        build_diagrams(DiagramInputs(index, _selection(index, "Service.Twice"), [], 4, full=True))
    )
    arrows = model["diagrams"][0]["arrows"]
    assert len(arrows) == 2
    assert {(a["sender"], a["receiver"], a["name"], a["path"], a["line"]) for a in arrows} == {
        ("Service", "Format", "Line", "App.cs", 21)
    }
    assert arrows[0]["id"] != arrows[1]["id"]


def test_the_side_changes_the_diagram_identifier(tmp_path: Path) -> None:
    index = _index(tmp_path)
    after = _by_arity(_model(index, Side.AFTER))[4]
    before = _by_arity(_model(index, Side.BEFORE))[4]
    assert after["id"] != before["id"]


def test_root_identifier_is_shared_by_both_sides(tmp_path: Path) -> None:
    index = _index(tmp_path)
    after = _by_arity(_model(index, Side.AFTER))[4]
    before = _by_arity(_model(index, Side.BEFORE))[4]
    assert after["root_id"] == before["root_id"]
    assert re.fullmatch(r"r_[0-9a-f]{12}", after["root_id"])
    assert re.fullmatch(r"d_[0-9a-f]{12}", after["id"])
    assert all(re.fullmatch(r"a_[0-9a-f]{12}", a["id"]) for a in after["arrows"])


def test_overloaded_roots_get_different_root_identifiers(tmp_path: Path) -> None:
    first, second = _model(_index(tmp_path))["diagrams"]
    assert first["root_id"] != second["root_id"]


def test_identities_pair_each_arrow_with_its_identifier(tmp_path: Path) -> None:
    index = _index(tmp_path)
    built = construct_diagrams(DiagramInputs(index, _selection(index), [], 4, full=True))
    diagram = next(d for d in built.diagrams if len(d.arrows) == 4)
    identified = identities_of(_single(diagram), Side.AFTER)[0]
    identities = identified.identities
    assert [i.arrow for i in identities.arrows] == diagram.arrows
    assert len({i.arrow_id for i in identities.arrows}) == 4


def test_a_diagram_refuses_identities_of_a_different_arrow_list(tmp_path: Path) -> None:
    index = _index(tmp_path)
    built = construct_diagrams(DiagramInputs(index, _selection(index), [], 4, full=True))
    diagram = next(d for d in built.diagrams if len(d.arrows) == 4)
    identified = identities_of(_single(diagram), Side.AFTER)[0]
    identities = identified.identities
    reordered = dataclasses.replace(diagram, arrows=list(reversed(diagram.arrows)))
    with pytest.raises(ValueError, match="食い違"):
        diagram_to_dict(IdentifiedDiagram(reordered, identities))
    shorter = dataclasses.replace(diagram, arrows=diagram.arrows[:-1])
    with pytest.raises(ValueError, match="食い違"):
        diagram_to_dict(IdentifiedDiagram(shorter, identities))


def test_moving_a_call_line_changes_the_arrow_identifier(tmp_path: Path) -> None:
    before = _by_arity(_model(_index(tmp_path)))[1]
    moved = SOURCE.replace(
        "    public void Run(string text)\n    {\n", "    public void Run(string text)\n    {\n\n"
    )
    after = _by_arity(_model(_index(tmp_path, moved)))[1]
    assert before["arrows"][0]["line"] + 1 == after["arrows"][0]["line"]
    assert before["arrows"][0]["id"] != after["arrows"][0]["id"]


def test_the_side_field_follows_the_argument() -> None:
    empty = DiagramSet((), DiagramSetNotes({}, (), ()))
    for side in (Side.BEFORE, Side.AFTER):
        assert diagrams_to_dict(identities_of(empty, side), empty.notes, side)["side"] is side


def test_identities_of_returns_only_the_identified_diagrams() -> None:
    notes = DiagramSetNotes({}, (), ("A.B",))
    assert identities_of(DiagramSet((), notes), Side.AFTER) == ()


def test_diagrams_to_dict_writes_the_given_notes() -> None:
    notes = DiagramSetNotes(
        {"r": (ArrowHit("D", 1), ParticipantHit("D", "P"))},
        (UnreachableRegion("J9", "a.py", "A.b", "s"),),
        ("A.B",),
    )
    result = diagrams_to_dict((), notes, Side.AFTER)
    assert result["region_index"] == {
        "r": [
            {"diagram": "D", "arrow": 1, "participant": None},
            {"diagram": "D", "arrow": None, "participant": "P"},
        ]
    }
    assert result["unreachable_regions"] == [
        {"id": "J9", "path": "a.py", "qualified_name": "A.b", "summary": "s"}
    ]
    assert result["dropped"] == ["A.B"]
