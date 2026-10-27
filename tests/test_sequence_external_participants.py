from pathlib import Path
from typing import Any

from review_fakes import changed_segment

from jigane.languages.python import PythonAdapter
from jigane.sequence.diagram import (
    EXTERNAL_PARTICIPANT_LIMIT,
    PARTICIPANT_LIMIT,
    build_diagrams,
    to_mermaid,
)
from jigane.sequence.index import build_index
from jigane.sequence.review_diagrams import sequences_of
from jigane.sequence.roots import changed_members, select_roots


def _diagram(tmp_path: Path, count: int) -> dict[str, Any]:
    """外部のモジュールの型を count 個、一つずつ呼ぶだけの入口 run の図。"""
    imports = "".join(f"from lib{n} import Ext{n}\n" for n in range(count))
    calls = "".join(f"    Ext{n}.go()\n" for n in range(count))
    (tmp_path / "app.py").write_text(f"{imports}\n\ndef run():\n{calls}", encoding="utf-8")
    adapter = PythonAdapter()
    index = build_index(tmp_path, ["app.py"], adapter)
    run = next(m for m in index.callables.values() if m.name == "run")
    declaration = run.declaration
    assert declaration is not None
    segment = changed_segment(
        "app.py",
        declaration.span.start_line,
        declaration.span.end_line,
        run.qualified_name.removeprefix("app."),
        "function",
    )
    changed = changed_members(index, [segment], adapter, include_tests=False)
    selection = select_roots(index, changed, adapter)
    model = build_diagrams(index, selection, [])
    diagram: dict[str, Any] = model["diagrams"][0]
    return diagram


def _externals(diagram: dict[str, Any]) -> list[str]:
    return [p["display"] for p in diagram["participants"] if p["external"]]


def test_entry_calling_only_external_types_gets_a_diagram(tmp_path: Path) -> None:
    diagram = _diagram(tmp_path, 2)
    assert [a["status"] for a in diagram["arrows"]] == ["external", "external"]
    assert _externals(diagram) == ["Ext0", "Ext1"]
    assert diagram["notes"] == []
    flows = sequences_of({"diagrams": [diagram]}, 1).flows
    assert len(flows) == 1


def test_six_external_participants_are_not_folded(tmp_path: Path) -> None:
    diagram = _diagram(tmp_path, EXTERNAL_PARTICIPANT_LIMIT)
    assert len(_externals(diagram)) == EXTERNAL_PARTICIPANT_LIMIT
    assert "外部" not in _externals(diagram)
    assert len(diagram["arrows"]) == EXTERNAL_PARTICIPANT_LIMIT


def test_seven_external_participants_fold_into_one(tmp_path: Path) -> None:
    diagram = _diagram(tmp_path, EXTERNAL_PARTICIPANT_LIMIT + 1)
    assert _externals(diagram) == ["外部"]
    assert [p["alias"] for p in diagram["participants"]] == ["P0", "P1"]
    assert {a["receiver"] for a in diagram["arrows"]} == {"外部"}
    assert len(diagram["arrows"]) == EXTERNAL_PARTICIPANT_LIMIT + 1
    assert "P0->>P1: go()" in to_mermaid(diagram)


def test_externals_fold_before_participant_limit_drops_arrows(tmp_path: Path) -> None:
    count = PARTICIPANT_LIMIT + 4
    diagram = _diagram(tmp_path, count)
    assert _externals(diagram) == ["外部"]
    assert len(diagram["arrows"]) == count
    assert diagram["notes"] == []


def test_entry_calling_index_and_external_types_draws_no_external_arrow(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "from lib import Ext\n\n\n"
        "class Helper:\n    def work(self):\n        return 1\n\n\n"
        "def run(helper: Helper):\n    helper.work()\n    Ext.go()\n",
        encoding="utf-8",
    )
    adapter = PythonAdapter()
    index = build_index(tmp_path, ["app.py"], adapter)
    segments = [
        changed_segment(
            "app.py",
            m.declaration.span.start_line,
            m.declaration.span.end_line,
            m.qualified_name.removeprefix("app."),
            "function",
        )
        for m in index.callables.values()
        if m.name in ("run", "work") and m.declaration is not None
    ]
    changed = changed_members(index, segments, adapter, include_tests=False)
    selection = select_roots(index, changed, adapter)
    model = build_diagrams(index, selection, [])
    diagram = next(d for d in model["diagrams"] if d["root"].endswith("run"))
    assert [a["status"] for a in diagram["arrows"]] == ["resolved"]
    assert _externals(diagram) == []
    assert diagram["notes"] == ["変更に関わらない呼び出し 1 件"]
