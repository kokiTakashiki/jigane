from pathlib import Path

from review_fakes import changed_segment

from jigane.languages.base import SequenceAdapter
from jigane.languages.swift import SwiftAdapter
from jigane.preconditions.required import required
from jigane.sequence.index import Index, build_index
from jigane.sequence.resolve import Resolved, extract_calls
from jigane.sequence.roots import changed_members, select_roots

PATH = "Run.swift"
SOURCE = """
protocol PresenterInput { func start() }
protocol PresenterOutput { func render() }

struct TimerView: View {
    let presenter: any PresenterInput & PresenterOutput
    let other: some PresenterOutput
    let stored: Int = 0

    var body: some View {
        Button("Start") { presenter.start() }
            .onAppear { presenter.render() }
    }
}
"""
adapter = SwiftAdapter()
conforming: SequenceAdapter = adapter


def _index(tmp_path: Path) -> Index:
    (tmp_path / PATH).write_text(SOURCE, encoding="utf-8")
    return build_index(tmp_path, [PATH], conforming)


def test_computed_property_is_a_callable_and_stored_property_is_not(tmp_path: Path) -> None:
    names = {m.name for m in _index(tmp_path).callables.values()}
    assert "body" in names
    assert not {"presenter", "other", "stored"} & names


def test_computed_property_is_a_root(tmp_path: Path) -> None:
    index = _index(tmp_path)
    body = next(m for m in index.callables.values() if m.name == "body")
    declaration = required(body.declaration)
    segment = changed_segment(
        PATH, body.line, body.line, declaration.qualified_name, declaration.kind
    )
    changed = changed_members(index, [segment], adapter, include_tests=False)
    assert [m.name for m in changed.values()] == ["body"]
    selection = select_roots(index, changed, adapter)
    assert [r.member.name for r in selection.roots] == ["body"]


def test_composition_receiver_resolves_each_component(tmp_path: Path) -> None:
    index = _index(tmp_path)
    body = next(m for m in index.callables.values() if m.name == "body")
    resolved = {
        c.name: c.resolution.member.qualified_name
        for c in extract_calls(index, body).calls
        if isinstance(c.resolution, Resolved) and c.resolution.member is not None
    }
    assert resolved["start"] == "PresenterInput.start"
    assert resolved["render"] == "PresenterOutput.render"


MIXED_PATH = "Mixed.swift"
MIXED_SOURCE = """
protocol Base { func shared() }
protocol Left { func left() }
protocol Right { func right() }

struct Holder {
    let one: any Base & Left
    let two: any Base & Right

    func run() {
        one.left()
        two.right()
        one.right()
    }
}
"""

IMPL_PATH = "Impl.swift"
IMPL_SOURCE = """
protocol Presenting { func start() }

final class Presenter: Presenting {
    func start() { print("x") }
}

struct Screen {
    let presenter: Presenting

    var body: Int {
        presenter.start()
        return 0
    }
}
"""


def test_compositions_sharing_a_head_do_not_mix_components(tmp_path: Path) -> None:
    (tmp_path / MIXED_PATH).write_text(MIXED_SOURCE, encoding="utf-8")
    index = build_index(tmp_path, [MIXED_PATH], conforming)
    run = next(m for m in index.callables.values() if m.name == "run")
    members = [
        c.resolution.member.qualified_name if c.resolution.member is not None else None
        for c in sorted(extract_calls(index, run).calls, key=lambda c: c.line)
        if isinstance(c.resolution, Resolved)
    ]
    # `any Base & Left` は right を持たないので、三つ目の呼び出しは解けない
    assert members == ["Left.left", "Right.right", None]


def test_implementation_called_through_protocol_is_not_dropped(tmp_path: Path) -> None:
    (tmp_path / IMPL_PATH).write_text(IMPL_SOURCE, encoding="utf-8")
    index = build_index(tmp_path, [IMPL_PATH], conforming)
    segments = [
        changed_segment(
            IMPL_PATH,
            m.line,
            m.line,
            required(m.declaration).qualified_name,
            required(m.declaration).kind,
        )
        for m in index.callables.values()
        if m.name in ("start", "body")
    ]
    changed = changed_members(index, segments, adapter, include_tests=False)
    selection = select_roots(index, changed, adapter)
    assert [r.member.name for r in selection.roots] == ["body"]
    assert selection.dropped == []


NO_CALL_SOURCE = """
struct Counter {
    var count: Int = 0
    var doubled: Int { count * 2 }
    var label: String { "n=\\(count)" }
    var shown: String { format(count) }
    func format(_ value: Int) -> String { "" }
}
"""


def test_computed_property_without_call_is_not_a_callable(tmp_path: Path) -> None:
    (tmp_path / PATH).write_text(NO_CALL_SOURCE, encoding="utf-8")
    index = build_index(tmp_path, [PATH], conforming)
    names = {m.name for m in index.callables.values()}
    assert "shown" in names
    assert not {"doubled", "label"} & names
