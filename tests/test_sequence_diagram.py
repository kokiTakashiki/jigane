import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes import register_no_sequence_adapter
from review_fakes import changed_segment, region_of

from jigane.commands.subcommands import sequence as sequence_command
from jigane.judgment_map.classification import Segment
from jigane.judgment_map.kinds import StoredMode
from jigane.judgment_map.map_kind import CodeMap
from jigane.judgment_map.region_table import LineCounts, Region, RegionTable
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.python import PythonAdapter
from jigane.sequence.diagram import build_diagrams, to_mermaid
from jigane.sequence.index import Index, build_index
from jigane.sequence.review_diagrams import build_model
from jigane.sequence.roots import (
    UNREACHABLE_ENTRY_LABEL,
    RootSelection,
    SequenceError,
    changed_members,
    select_roots,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sequence" / "roots"
APP = "App.cs"
TESTS = "App.Tests/AppTests.cs"

CHANGED = [
    "View.Show",
    "Handler.OnEvent",
    "Chain4.Four",
    "Chain3.Three",
    "Chain2.Two",
    "Service.Run",
    "Service.Helper",
    "Service.Loop",
    "App.Main",
]


@pytest.fixture(scope="module")
def index() -> Index:
    return build_index(FIXTURES, [APP, TESTS], CSharpAdapter())


def _segments(index: Index, names: list[str], path: str = APP) -> list[Segment]:
    segments: list[Segment] = []
    for member in index.callables.values():
        declaration = member.declaration
        if member.path == path and declaration is not None and member.qualified_name in names:
            line = declaration.span.start_line
            segments.append(changed_segment(path, line, line, member.qualified_name))
    return segments


def _selection(index: Index, names: list[str] = CHANGED, *, tests: bool = False) -> RootSelection:
    segments = _segments(index, names) + _segments(index, ["AppTests.RunsMain"], TESTS)
    changed = changed_members(index, segments, CSharpAdapter(), include_tests=tests)
    return select_roots(index, changed, CSharpAdapter())


def _regions() -> list[Region]:
    def region(number: int, name: str) -> Region:
        return region_of({"id": f"J{number}", "path": APP, "qualified_name": name})

    return [
        region(1, "Service.Run"),
        region(2, "Service"),
        region(3, "App.view"),
        region(4, "Format"),
        region(5, "Handler.OnEvent"),
    ]


def _root_names(selection: RootSelection) -> list[str]:
    return [r.member.qualified_name for r in selection.roots]


def test_roots_exclude_callees_interface_implementations_and_lambda_handlers(index: Index) -> None:
    # Show は IView 経由で Main から呼ばれ、OnEvent はラムダの中でしか呼ばれない
    assert _root_names(_selection(index)) == ["App.Main"]


def test_uncalled_interface_implementation_is_a_labelled_root(index: Index) -> None:
    selection = _selection(index, ["View.Show"])
    assert _root_names(selection) == ["View.Show"]
    assert selection.roots[0].labels == (UNREACHABLE_ENTRY_LABEL,)


def test_test_paths_are_excluded_unless_included(index: Index) -> None:
    assert _root_names(_selection(index, ["App.Main"])) == ["App.Main"]
    # テストの宣言を含めても、テストからの呼び出しは Main を根から外さない
    assert _root_names(_selection(index, ["App.Main"], tests=True)) == [
        "App.Main",
        "AppTests.RunsMain",
    ]


def test_callee_whose_only_callers_are_tests_stays_a_root(index: Index) -> None:
    # Service.Run は Main（変更に含めない）からしか呼ばれないが、RunsMain は Main を呼ぶ
    assert _root_names(_selection(index, ["Service.Run"], tests=True)) == [
        "AppTests.RunsMain",
        "Service.Run",
    ]


def test_entry_point_is_a_root_even_when_a_changed_function_calls_it(tmp_path: Path) -> None:
    (tmp_path / "cli.py").write_text(
        "def main():\n    pass\n\n\ndef checks():\n    main()\n\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )
    adapter = PythonAdapter()
    index = build_index(tmp_path, ["cli.py"], adapter)
    segments = [
        replace(segment, qualified_name=segment.qualified_name.removeprefix("cli."))
        for segment in _segments(index, ["cli.main", "cli.checks", "cli.helper"], "cli.py")
    ]
    changed = changed_members(index, segments, adapter, include_tests=False)
    selection = select_roots(index, changed, adapter)
    # main は呼び手を持っていても入口で、他の根より先に並ぶ
    assert _root_names(selection) == ["cli.main", "cli.checks", "cli.helper"]


def _model(index: Index, depth: int = 4, *, full: bool = False) -> dict[str, Any]:
    return build_diagrams(index, _selection(index), _regions(), depth, full=full)


def _diagram(model: dict[str, Any]) -> dict[str, Any]:
    return next(d for d in model["diagrams"] if d["root"] == "App.Main")


def test_slicing_collapses_unrelated_calls_and_full_disables_it(index: Index) -> None:
    sliced = _diagram(_model(index))
    assert any(n.startswith("変更に関わらない呼び出し") for n in sliced["notes"])
    assert "Log" not in [a["name"] for a in sliced["arrows"]]
    full = _diagram(_model(index, full=True))
    assert [a["name"] for a in full["arrows"]].count("Log") == 2
    assert not any("変更に関わらない" in n for n in full["notes"])


def test_depth_counts_participant_changes_and_private_calls_expand_inline(index: Index) -> None:
    shallow = _diagram(_model(index, depth=1))
    names = [a["name"] for a in shallow["arrows"]]
    assert "Run" in names
    assert "Two" in names
    assert "Three" not in names
    assert any("深さの上限 1" in n for n in shallow["notes"])
    deep = _diagram(_model(index, depth=4))
    helper = next(a for a in deep["arrows"] if a["name"] == "Helper")
    run = next(a for a in deep["arrows"] if a["name"] == "Run")
    assert helper["sender"] == helper["receiver"] == "Service"
    assert helper["depth"] == run["depth"]


def test_recursion_draws_the_arrow_without_descending(index: Index) -> None:
    arrows = _diagram(_model(index))["arrows"]
    loops = [a for a in arrows if a["name"] == "Loop"]
    assert [a["sender"] for a in loops] == ["App", "Service"]


def test_labels_and_reverse_index(index: Index) -> None:
    model = _model(index)
    diagram = _diagram(model)
    run = next(a for a in diagram["arrows"] if a["name"] == "Run")
    assert run["region"] == "J1"
    service = next(p for p in diagram["participants"] if p["qualified_name"] == "Service")
    assert service["regions"] == ["J2"]
    app = next(p for p in diagram["participants"] if p["qualified_name"] == "App")
    assert app["regions"] == ["J3"]
    entry = model["region_index"]["J1"][0]
    assert entry["diagram"] == "App.Main"
    assert entry["arrow"] == diagram["arrows"].index(run) + 1


def test_unreachable_regions_are_listed(index: Index) -> None:
    # Handler.OnEvent はラムダの中でしか呼ばれず、Format は参加者にならない
    unreachable = [r["id"] for r in _model(index)["unreachable_regions"]]
    assert unreachable == ["J4", "J5"]


def test_mermaid_for_a_small_fixture(index: Index) -> None:
    selection = _selection(index, ["Service.Run", "Service.Helper"])
    model = build_diagrams(index, selection, _regions())
    assert model["diagrams"][0]["root"] == "Service.Run"
    assert to_mermaid(model["diagrams"][0]) == (
        "sequenceDiagram\n"
        "    participant P0 as Service [J2]\n"
        "    P0->>P0: Helper()\n"
        "    Note over P0: 変更に関わらない呼び出し 3 件\n"
    )


def _tagged_diagram(regions: list[str]) -> dict[str, Any]:
    participant = {
        "alias": "P0",
        "qualified_name": "Engine",
        "display": "Engine",
        "regions": regions,
    }
    return {"participants": [participant], "arrows": [], "labels": [], "notes": []}


def test_participant_tag_folds_many_regions_into_a_count() -> None:
    ids = [f"J{n}" for n in range(1, 15)]
    assert "participant P0 as Engine [J1 J2 J3 ほか 11 件]\n" in to_mermaid(_tagged_diagram(ids))


def test_participant_tag_folds_regions_that_are_not_consecutive() -> None:
    ids = ["J2", "J5", "J9", "J11"]
    assert "participant P0 as Engine [J2 J5 J9 ほか 1 件]\n" in to_mermaid(_tagged_diagram(ids))


def test_participant_tag_keeps_regions_up_to_the_cap() -> None:
    ids = ["J1", "J2", "J3"]
    assert "participant P0 as Engine [J1] [J2] [J3]\n" in to_mermaid(_tagged_diagram(ids))
    assert "participant P0 as Engine\n" in to_mermaid(_tagged_diagram([]))


def test_unresolved_calls_use_the_cross_arrow_in_full_mode(index: Index) -> None:
    diagram = _diagram(_model(index, full=True))
    assert any(a["status"] == "unresolved" for a in diagram["arrows"])
    assert "-x" in to_mermaid(diagram)


class _Review:
    pull_request = None

    def __init__(self, paths: list[str], root: str) -> None:
        self.map_kind = CodeMap(StoredMode.FEATURE, [], RegionTable([], LineCounts.empty()))
        self.paths = paths
        self.repository_root = root


def test_cli_rejects_a_review_without_a_sequence_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_no_sequence_adapter(monkeypatch)
    with pytest.raises(SequenceError, match="呼び出しの抽出がまだありません"):
        build_model(_Review(["A.nosq"], "."), None, None, 4, full=False, include_tests=False)  # type: ignore[arg-type]


def test_cli_rejects_depth_out_of_range() -> None:
    review = _Review([APP], str(FIXTURES))
    with pytest.raises(SequenceError, match="--depth"):
        build_model(review, None, None, 9, full=False, include_tests=False)  # type: ignore[arg-type]


def test_cli_arguments() -> None:
    parser = argparse.ArgumentParser()
    sequence_command.register(parser.add_subparsers())
    args = parser.parse_args(["sequence", "r#1", "--depth", "2", "--full", "--format", "json"])
    assert (args.depth, args.full, args.format, args.side) == (2, True, "json", "both")
    assert parser.parse_args(["sequence", "r#1", "--side", "before"]).side == "before"
    with pytest.raises(SystemExit):
        parser.parse_args(["sequence", "r#1", "--side", "middle"])
    with pytest.raises(SystemExit):
        parser.parse_args(["sequence", "r#1", "--root", "A.B", "--region", "J1"])


def _cycle_roots(tmp_path: Path, calls: dict[str, str], names: list[str]) -> list[str]:
    """calls は メソッド名から呼び先の名前への対応。本体が複数行でないと索引は本体を持たない。"""
    methods = "".join(f" void {m}()\n {{\n {c}();\n }}\n" for m, c in calls.items())
    (tmp_path / "C.cs").write_text("class C {\n" + methods + "}\n", encoding="utf-8")
    cycle_index = build_index(tmp_path, ["C.cs"], CSharpAdapter())
    segments = _segments(cycle_index, names, "C.cs")
    changed = changed_members(cycle_index, segments, CSharpAdapter(), include_tests=False)
    return _root_names(select_roots(cycle_index, changed, CSharpAdapter()))


def test_two_cycle_without_external_caller_yields_one_root(tmp_path: Path) -> None:
    assert _cycle_roots(tmp_path, {"A": "B", "B": "A"}, ["C.A", "C.B"]) == ["C.A"]


def test_three_cycle_without_external_caller_yields_one_root(tmp_path: Path) -> None:
    calls = {"A": "B", "B": "D", "D": "A"}
    assert _cycle_roots(tmp_path, calls, ["C.A", "C.B", "C.D"]) == ["C.A"]


def test_cycle_called_from_a_changed_member_is_not_a_root(tmp_path: Path) -> None:
    calls = {"E": "A", "A": "B", "B": "A"}
    assert _cycle_roots(tmp_path, calls, ["C.E", "C.A", "C.B"]) == ["C.E"]
