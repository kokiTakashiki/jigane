from pathlib import Path

from review_fakes import changed_segment, region_of

from jigane.languages.base import CallSite, SequenceAdapter
from jigane.languages.swift import SwiftAdapter
from jigane.preconditions.required import required
from jigane.sequence.diagram import build_diagrams, to_mermaid
from jigane.sequence.index import Index, Member, TypeKey, build_index
from jigane.sequence.resolve import External, Extraction, Resolved, Unresolved, extract_calls
from jigane.sequence.roots import changed_members, select_roots

PATH = "Run.swift"
SOURCE = """
protocol Store {
    func save(_ value: Int)
}

class Engine {
    func start() {}
    func fmt(a: Int) {}
    func fmt(b: Int) {}
    func fmt(c: Int) {}
}

extension Engine {
    func stop() {
        start()
    }
}

class Runner {
    let engine: Engine

    func run(store: any Store, engines: [Engine]) {
        let local: Engine? = makeEngine()
        local?.start()
        local!.stop()
        let generic = Array<Int>()
        generic.append(1)
        store.save(1)
        for e in engines { e.start() }
        engine.fmt(b: 1)
        engine.fmt(c: 1)
        let w = Engine.init()
        w.fmt(a: 1)
        mystery.go()
    }

    func makeEngine() -> Engine? { return nil }
}
"""
adapter = SwiftAdapter()
conforming: SequenceAdapter = adapter


def _run(tmp_path: Path) -> tuple[Index, Member, Extraction]:
    (tmp_path / PATH).write_text(SOURCE, encoding="utf-8")
    index = build_index(tmp_path, [PATH], conforming)
    member, _, _ = index.lookup(index.types[TypeKey("Runner")], "run", ("method",))
    assert member is not None
    return index, member, extract_calls(index, member)


def test_extension_member_is_merged_into_the_original_type(tmp_path: Path) -> None:
    index, _, extraction = _run(tmp_path)
    assert {"start", "stop", "fmt"} <= set(index.types[TypeKey("Engine")].members)
    resolution = next(c.resolution for c in extraction.calls if c.name == "stop")
    assert isinstance(resolution, Resolved)
    assert resolution.type == TypeKey("Engine")
    assert required(resolution.member).name == "stop"


def test_receivers_resolve_from_local_and_parameter_annotations(tmp_path: Path) -> None:
    _, _, extraction = _run(tmp_path)
    resolved = {
        (c.name, c.resolution.type) for c in extraction.calls if isinstance(c.resolution, Resolved)
    }
    # local: Engine?、store: any Store、w = Engine.init() の初期化式
    assert {
        ("start", TypeKey("Engine")),
        ("save", TypeKey("Store")),
        ("fmt", TypeKey("Engine")),
    } <= resolved
    # for-in の e は型が分からず、mystery は宣言が無い
    unresolved = [c.name for c in extraction.calls if isinstance(c.resolution, Unresolved)]
    assert unresolved.count("start") == 1
    assert "go" in unresolved


def test_generic_creation_is_an_external_type(tmp_path: Path) -> None:
    _, _, extraction = _run(tmp_path)
    appends = [c.resolution for c in extraction.calls if c.name == "append"]
    assert appends == [External("Array")]
    assert any(c.is_creation and c.name == "Array" for c in extraction.calls)


def test_labels_narrow_overloaded_calls(tmp_path: Path) -> None:
    _, _, extraction = _run(tmp_path)
    picked = [
        (required(c.resolution.member).parameters[0].label, c.resolution.candidates)
        for c in extraction.calls
        if c.name == "fmt" and isinstance(c.resolution, Resolved)
    ]
    assert picked == [("b", 1), ("c", 1), ("a", 1)]


def test_diagram_is_generated(tmp_path: Path) -> None:
    index, run, _ = _run(tmp_path)
    # 拡張で足された stop も変更したことにして、呼び先として図に現れるようにする
    stop = required(index.lookup(index.types[TypeKey("Engine")], "stop", ("method",))[0])
    segments = []
    for member in (run, stop):
        declaration = required(member.declaration)
        line = declaration.span.start_line
        segments.append(changed_segment(PATH, line, line, declaration.qualified_name))
    changed = changed_members(index, segments, adapter, include_tests=False)
    selection = select_roots(index, changed, adapter)
    regions = [region_of({"id": "J1", "path": PATH, "qualified_name": "Runner.run"})]
    model = build_diagrams(index, selection, regions)
    text = to_mermaid(model["diagrams"][0])
    assert "Runner" in text
    assert "Engine" in text
    assert "stop" in text


def test_array_annotation_is_the_container_not_the_element(tmp_path: Path) -> None:
    (tmp_path / PATH).write_text(
        "class Foo { func append() {} }\nclass R { func run(xs: [Foo]) { xs.append() } }\n",
        encoding="utf-8",
    )
    index = build_index(tmp_path, [PATH], conforming)
    member, _, _ = index.lookup(index.types[TypeKey("R")], "run", ("method",))
    assert member is not None
    calls = extract_calls(index, member).calls
    assert [c.resolution for c in calls] == [External("Array")]


def test_bind_uses_the_external_label_of_a_renamed_parameter(tmp_path: Path) -> None:
    (tmp_path / PATH).write_text(
        "class R {\n"
        "    func take(at store: Int, _ other: Int) {}\n"
        "    func run() { take(at: 1, 2) }\n"
        "}\n",
        encoding="utf-8",
    )
    index = build_index(tmp_path, [PATH], conforming)
    take, _, _ = index.lookup(index.types[TypeKey("R")], "take", ("method",))
    run, _, _ = index.lookup(index.types[TypeKey("R")], "run", ("method",))
    assert take is not None
    assert run is not None
    site = next(
        c for c in index.adapter.call_sites(required(index.adapter.body_of(run.node)), b"")
    )
    assert isinstance(site, CallSite)
    pairs = take.bind(site.arguments, positional_labels=True)
    assert [(p.name, a.name) for p, a in pairs] == [("store", "at"), ("other", None)]
    # 位置に結び付かない言語の規則では名前付きの引数を内部名で引くので、外部名 at は束縛されない
    assert [p.name for p, _ in take.bind(site.arguments, positional_labels=False)] == ["other"]
