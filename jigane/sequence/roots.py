"""変更した宣言の集合 C と、C の中の呼び出しの辺と、根（4.2.1 節）を求める。C# 専用。"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from jigane.figures.models import NoteText
from jigane.judgment_map.classification import Segment
from jigane.languages.base import SequenceAdapter
from jigane.languages.registry import is_test_path
from jigane.sequence.index import Index, Member, TypeKey
from jigane.sequence.resolve import Call, Extraction, Resolved, extract_calls

UNREACHABLE_ENTRY_LABEL = "呼び手を静的に見つけられない入口の候補"

Key = tuple[str, int]


class Side(StrEnum):
    """図や変更の集合をどちらの木から取るか。値は CLI の --side の文字列と JSON の side に出る。"""

    BEFORE = "before"
    AFTER = "after"
    BOTH = "both"


class SequenceError(ValueError):
    """図を作れない理由。str は利用者に見せる敬体で、note は資料に入る常体の文も持つ。"""

    def __init__(self, note: NoteText) -> None:
        super().__init__(note.display)
        self.note = note


def key_of(member: Member) -> Key:
    return (member.path, member.line)


@dataclass(frozen=True)
class Root:
    member: Member
    labels: tuple[str, ...] = ()


@dataclass
class RootSelection:
    changed: dict[Key, Member]
    roots: list[Root]
    # 入口にも、他の変更した宣言の図の矢印にもならず、どの図にも載らない変更した宣言
    dropped: list[Member] = field(default_factory=list)


def changed_members(
    index: Index,
    segments: Iterable[Segment],
    adapter: SequenceAdapter,
    *,
    include_tests: bool,
    side: Literal[Side.BEFORE, Side.AFTER] = Side.AFTER,
) -> dict[Key, Member]:
    """判断の地図の segment のうち、索引の木で本体を持つメソッドを索引のメンバへ写す。

    side は AFTER（変更後の側の path と span を見る）か BEFORE（変更前の側を見る）。
    BOTH は型で受けない。
    """
    by_path: dict[str, list[Member]] = {}
    for member in index.callables.values():
        by_path.setdefault(member.path, []).append(member)
    found: dict[Key, Member] = {}
    before = side is Side.BEFORE
    for segment in segments:
        path = segment.old_path if before else segment.new_path
        span = segment.old_span if before else segment.new_span
        if (
            segment.declaration_kind not in ("method", "function", "variable", "property")
            or path is None
            or span is None
        ):
            continue
        if not include_tests and is_test_path(path, [adapter]):
            continue
        for member in by_path.get(path, []):
            declaration = member.declaration
            if declaration is None or declaration.body is None:
                continue
            overlaps = (
                declaration.span.start_line <= span.end_line
                and span.start_line <= declaration.span.end_line
            )
            if declaration.qualified_name == segment.qualified_name and overlaps:
                found[key_of(member)] = member
    return found


def is_interface_member(index: Index, member: Member) -> bool:
    info = index.types.get(member.owner)
    return info is not None and info.kind == "interface"


def interface_members(index: Index, member: Member) -> list[Member]:
    """member が実装しているインターフェースの、同名のメソッド。"""
    info = index.types.get(member.owner)
    if info is None or info.kind == "interface":
        return []
    found: list[Member] = []
    for base in index.hierarchy(info):
        if base.kind == "interface" and base is not info:
            found.extend(m for m in base.members.get(member.name, ()) if m.kind == "method")
    return found


def implementations(index: Index, interface_member: Member) -> list[Member]:
    """インターフェースのメンバを実装する、索引の中の同名のメソッド。"""
    found: list[Member] = []
    for info in index.types.values():
        if info.kind == "interface":
            continue
        if any(base.key == interface_member.owner for base in index.hierarchy(info)):
            found.extend(
                m for m in info.members.get(interface_member.name, ()) if m.kind == "method"
            )
    return found


def resolved_member(call: Call) -> Member | None:
    resolution = call.resolution
    return resolution.member if isinstance(resolution, Resolved) else None


def calls_with_bindings(
    index: Index,
    member: Member,
    bindings: Mapping[str, TypeKey],
    extraction: Extraction | None = None,
) -> list[Call]:
    """member の本体の呼び出し。規則 9 の対応を持つ呼び出しは、呼び先の呼び出しを一段分加える。

    extraction は member について抽出済みの結果。渡せば再抽出しない。
    """
    calls = list((extraction or extract_calls(index, member, bindings)).calls)
    expanded = list(calls)
    for call in calls:
        callee = resolved_member(call)
        if call.bindings and callee is not None:
            expanded.extend(extract_calls(index, callee, dict(call.bindings)).calls)
    return expanded


@dataclass
class _TarjanState:
    """Tarjan 法の走査の途中経過。"""

    graph: Mapping[Key, set[Key]]
    order: dict[Key, int] = field(default_factory=dict)
    low: dict[Key, int] = field(default_factory=dict)
    stack: list[Key] = field(default_factory=list)
    on_stack: set[Key] = field(default_factory=set)
    work: list[tuple[Key, Iterator[Key]]] = field(default_factory=list)

    def enter(self, node: Key) -> None:
        """node を訪問済みにし、その後続の走査を作業の列の末尾へ積む。"""
        self.order[node] = self.low[node] = len(self.order)
        self.stack.append(node)
        self.on_stack.add(node)
        self.work.append((node, iter(sorted(self.graph[node]))))

    def first_unvisited_successor(self, node: Key, successors: Iterator[Key]) -> Key | None:
        """未訪問の最初の後続を返す。そこまでに見た、スタックにある後続は low に反映する。"""
        for succ in successors:
            if succ not in self.order:
                return succ
            if succ in self.on_stack:
                self.low[node] = min(self.low[node], self.order[succ])
        return None

    def pop_component(self, root: Key) -> list[Key]:
        """スタックから root までを取り出し、一つの強連結成分にする。"""
        component: list[Key] = []
        while True:
            member_key = self.stack.pop()
            self.on_stack.discard(member_key)
            component.append(member_key)
            if member_key == root:
                break
        return component


def _components(graph: Mapping[Key, set[Key]]) -> list[list[Key]]:
    """有向グラフの強連結成分（Tarjan 法。再帰を使わない）。"""
    state = _TarjanState(graph)
    result: list[list[Key]] = []
    for start in graph:
        if start in state.order:
            continue
        state.enter(start)
        while state.work:
            node, successors = state.work[-1]
            child = state.first_unvisited_successor(node, successors)
            if child is not None:
                state.enter(child)
                continue
            state.work.pop()
            if state.work:
                parent = state.work[-1][0]
                state.low[parent] = min(state.low[parent], state.low[node])
            if state.low[node] == state.order[node]:
                result.append(state.pop_component(node))
    return result


@dataclass
class _CallFacts:
    """変更した宣言の本体の呼び出しから集めた事実。"""

    # 変更した宣言どうしの呼び出しの辺（図の矢印になるもの）
    graph: dict[Key, set[Key]]
    # 呼び出されたインターフェースのメンバ
    called_interface: set[Key]
    # 遅延して呼ばれる参照の先
    deferred_targets: set[Key]


def _collect_call_facts(
    index: Index,
    changed: Mapping[Key, Member],
    adapter: SequenceAdapter,
) -> _CallFacts:
    called_interface: set[Key] = set()
    deferred_targets: set[Key] = set()
    graph: dict[Key, set[Key]] = {key: set() for key in changed}
    for key, member in changed.items():
        extraction = extract_calls(index, member)
        for call in calls_with_bindings(index, member, {}, extraction):
            callee = resolved_member(call)
            if callee is None:
                continue
            if is_interface_member(index, callee):
                called_interface.add(key_of(callee))
            if (
                key_of(callee) in changed
                and key_of(callee) != key
                and not is_test_path(member.path, [adapter])
            ):
                graph[key].add(key_of(callee))
        # 遅延して呼ばれる参照は、図の矢印にはしないが、根から外す理由になる
        for reference in extraction.deferred:
            for target in reference.targets:
                if isinstance(target, Resolved) and target.member is not None:
                    deferred_targets.add(key_of(target.member))
    return _CallFacts(graph, called_interface, deferred_targets)


def _called_from_outside(graph: Mapping[Key, set[Key]], components: list[list[Key]]) -> set[Key]:
    """自分の成分の外から辺で呼ばれる宣言。"""
    component_of: dict[Key, int] = {}
    for number, component in enumerate(components):
        for key in component:
            component_of[key] = number
    return {
        callee
        for key, callees in graph.items()
        for callee in callees
        if component_of[callee] != component_of[key]
    }


def _implements_called_interface(index: Index, member: Member, facts: _CallFacts) -> bool:
    return any(key_of(i) in facts.called_interface for i in interface_members(index, member))


def _represent_component(
    index: Index,
    changed: Mapping[Key, Member],
    facts: _CallFacts,
    component: list[Key],
) -> tuple[Root | None, list[Member]]:
    """成分の代表の根と、根にならず図にも載らない宣言を返す。代表が居なければ根は None。"""
    # 遅延参照や呼ばれるインターフェースで根から外れるメンバーは代表にしない
    eligible = [
        changed[key]
        for key in component
        if key not in facts.deferred_targets
        and not _implements_called_interface(index, changed[key], facts)
    ]
    if not eligible:
        # 呼ばれるインターフェースの実装は、呼び手の図で矢印になる（diagram.py）。
        # 遅延参照だけが根から外した宣言は、どの図の矢印にもならない
        dropped = [
            changed[key]
            for key in component
            if not _implements_called_interface(index, changed[key], facts)
        ]
        return None, dropped
    member = min(eligible, key=lambda m: (m.qualified_name, m.path, m.line))
    interfaces = interface_members(index, member)
    labels = (UNREACHABLE_ENTRY_LABEL,) if interfaces else ()
    return Root(member, labels), []


def select_roots(
    index: Index,
    changed: Mapping[Key, Member],
    adapter: SequenceAdapter,
) -> RootSelection:
    """変更した宣言から入口を選ぶ。

    テストのファイルの宣言からの呼び出しは、呼ばれる側を入口から外す理由にしない。
    アダプタが入口と見なす名前の宣言は、呼び手の有無によらず入口にする。
    """
    facts = _collect_call_facts(index, changed, adapter)
    # 呼び合いの輪は一つの成分へ畳み、成分の外からの辺を持たない成分から代表を一つ採る（4.2.1 節）
    components = _components(facts.graph)
    called_from_outside = _called_from_outside(facts.graph, components)
    entry_keys = {
        key for key, member in changed.items() if member.name in adapter.entry_point_names
    }
    candidates: list[Root] = [Root(changed[key]) for key in entry_keys]
    dropped: list[Member] = []
    for component in components:
        # 入口の名前の宣言を含む成分は、その宣言を入口として上で採り済み
        if entry_keys.intersection(component):
            continue
        if any(key in called_from_outside for key in component):
            continue
        root, skipped = _represent_component(index, changed, facts, component)
        dropped.extend(skipped)
        if root is not None:
            candidates.append(root)
    # 図の並びは、プログラムの入口を先にする
    candidates.sort(
        key=lambda r: (
            r.member.name not in adapter.entry_point_names,
            r.member.qualified_name,
            r.member.path,
            r.member.line,
        )
    )
    dropped.sort(key=lambda m: (m.qualified_name, m.path, m.line))
    return RootSelection(dict(changed), candidates, dropped)
