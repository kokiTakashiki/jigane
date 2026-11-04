from pathlib import Path
from typing import Any

from review_fakes import changed_segment

from jigane.languages.python import PythonAdapter
from jigane.sequence.diagram import UNRESOLVED_TARGET_LIMIT, build_diagrams, to_mermaid
from jigane.sequence.index import build_index
from jigane.sequence.review_diagrams import sequences_of
from jigane.sequence.roots import changed_members, select_roots

LIB = """
def make():
    return 1
"""

SOURCE = """
import asyncio
import lib
from lib import make


def helper(x):
    return x


def total_units(items):
    return helper(items)


def save_all(first, second):
    lib.a.save()
    lib.a.save()
    make.b.load()


def many_targets(first):
    lib.a.one()
    lib.a.two()
    lib.a.three()
    lib.a.four()
    lib.a.five()
    lib.a.six()
    lib.a.seven()


def outside_only(path):
    asyncio.sleep(1)
    open(path)
    print(path)
    return []


def alone(value):
    return value
"""


def _model(tmp_path: Path, names: list[str]) -> dict[str, Any]:
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "lib.py").write_text(LIB, encoding="utf-8")
    adapter = PythonAdapter()
    index = build_index(tmp_path, ["app.py", "lib.py"], adapter)
    segments = []
    for name in names:
        member = next(m for m in index.callables.values() if m.name == name)
        declaration = member.declaration
        assert declaration is not None
        segments.append(
            changed_segment(
                "app.py", declaration.span.start_line, declaration.span.end_line, name, "function"
            )
        )
    changed = changed_members(index, segments, adapter, include_tests=False)
    return build_diagrams(index, select_roots(index, changed, adapter), [])


def _by_root(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {d["root"]: d for d in model["diagrams"]}


def test_standalone_function_entry_is_drawn_with_the_functions_it_calls(tmp_path: Path) -> None:
    model = _model(tmp_path, ["total_units"])
    diagram = _by_root(model)["app.total_units"]
    assert [(a["name"], a["status"]) for a in diagram["arrows"]] == [("helper", "resolved")]
    figure = sequences_of(model, 1)
    assert [flow.title.split("（")[0] for flow in figure.flows] == ["app.total_units"]
    assert "P0->>P0: helper()" in figure.flows[0].panels[0].mermaid
    assert figure.notes == ()


def test_unresolved_call_is_drawn_as_an_unresolved_target(tmp_path: Path) -> None:
    model = _model(tmp_path, ["save_all"])
    diagram = _by_root(model)["app.save_all"]
    displays = [p["display"] for p in diagram["participants"]]
    assert displays == ["app", "lib.a.save（解けない呼び先）", "make.b.load（解けない呼び先）"]
    mermaid = to_mermaid(diagram)
    assert "participant P1 as lib.a.save（解けない呼び先）" in mermaid
    # 解けた呼び出しの "->>" と見分けの付く破線の矢印で、同じ呼びの式は同じ参加者に集まる
    assert mermaid.count("P0--xP1: save()") == 2
    assert "P0--xP2: load()" in mermaid
    assert sequences_of(model, 1).notes == ()


def test_unresolved_targets_beyond_the_limit_become_a_count(tmp_path: Path) -> None:
    diagram = _by_root(_model(tmp_path, ["many_targets"]))["app.many_targets"]
    targets = [p for p in diagram["participants"] if p["qualified_name"].startswith("?")]
    assert len(targets) == UNRESOLVED_TARGET_LIMIT
    assert diagram["notes"] == [
        f"解けない呼び先の上限 {UNRESOLVED_TARGET_LIMIT} を超える呼び出し 2 件"
    ]


def test_entry_without_any_call_keeps_the_not_drawn_note(tmp_path: Path) -> None:
    model = _model(tmp_path, ["alone"])
    figure = sequences_of(model, 1)
    assert figure.flows == ()
    assert figure.notes[0].material == "呼び出しの無い入口 1 件は図にしない: app.alone"


def test_calls_outside_the_repository_are_not_drawn(tmp_path: Path) -> None:
    model = _model(tmp_path, ["outside_only"])
    figure = sequences_of(model, 1)
    assert figure.flows == ()
    assert figure.notes[0].material.startswith(
        "呼び先を解けない呼び出しがある入口 1 件は図にしない"
    )
    diagram = _by_root(model)["app.outside_only"]
    assert diagram["arrows"] == []
