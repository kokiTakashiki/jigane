"""方針のレビューに渡す並行処理の図。変更した関数ごとの、隔離の領域を列にしたシーケンス図。

関数の並行の手順（await、構造化された並行、構造化されない Task、キュー、ブロックする待ち合わせ）を
言語アダプタの ConcurrencyAdapter から受け取り、領域をまたぐ await だけを矢印にする。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node

from jigane.figures.models import MermaidFigure, Panel
from jigane.git.tree_files import walk_tree_files
from jigane.languages import registry
from jigane.languages.base import (
    STRUCTURED_KIND_MEANINGS,
    ActorDomain,
    AwaitCall,
    BlockingWait,
    Body,
    ConcurrencyAdapter,
    ConcurrencyStep,
    ContinueOn,
    EventLoopDomain,
    GlobalActorDomain,
    Isolation,
    MemberAssignment,
    NonisolatedDomain,
    QueueDispatch,
    QueueDomain,
    Sharing,
    StructuredConcurrency,
    SyncCall,
    TaskKind,
    UiThreadDomain,
    UnstructuredTask,
)
from jigane.reviews.lifecycle import Review
from jigane.sequence.diagram import mermaid_text
from jigane.sequence.index import Index, Member, TypeInfo, build_index
from jigane.sequence.resolve import Call, call_resolver, receiver_typer
from jigane.sequence.roots import changed_members, resolved_member

SECTION_CONCURRENCY = "並行処理の図"
LEGEND = (
    "変更した関数のうち、await やタスクなどの並行の構文を持つものを、一関数につき一枚の図にした。"
    "列は実行の領域（actor、MainActor、グローバル actor、UI スレッド、イベントループ、"
    "隔離されない領域、キューやスレッドプール）で、種類ごとに枠でくくる。"
    "矢印は領域をまたぐ呼び出しと投げ込みだけで、同じ領域の中の呼び出しと、"
    "呼び先を決められない呼び出しは描かない。"
    "`par` は構造化された並行（{}）の並びで、見出しは言語の呼び名。"
    "枝の中だけは、同じ領域や呼び先を決められない呼び出しも自分への矢印にする。"
    "`opt Task` は構造化されないタスク（Task、create_task）の中の手順。"
    "「注意:」で始まる注記は、親の取り消しが伝わらない Task や、協調スレッドと主スレッドを塞ぐ"
    "待ち合わせ、主スレッドの外からの UI の更新（await の無い呼び出しと代入）、"
    "領域をまたぐ await に渡す、Sendable に適合しない class や var を持つ参照型の値"
    "（宣言から確かめられたものだけ）など、危うい形を指す。"
    "関数の領域は、関数の注記、無ければ所属する型の注記、どちらも無ければ隔離されない領域。"
).format("、".join(STRUCTURED_KIND_MEANINGS.values()))
_BOXES = (
    "actor",
    "MainActor",
    "グローバル actor",
    "UI スレッド",
    "イベントループ",
    "隔離されない領域",
    "キュー",
)
_UNSTRUCTURED_NOTE = "注意: 構造化されない Task（親の取り消しが伝わらない）"
_UI_NOTE = "注意: 主スレッドの外からの UI の更新"
_SHARING_NOTE = "注意: actor をまたいで共有される"
_SHARING_REASONS = {
    Sharing.NOT_SENDABLE: "Sendable に適合しない",
    Sharing.MUTABLE: "可変の項目を持つ",
}
_INDENT = "    "


def _box(domain: Isolation) -> str:
    match domain:
        case GlobalActorDomain(name=name):
            return "MainActor" if name == "MainActor" else "グローバル actor"
        case ActorDomain():
            return "actor"
        case UiThreadDomain():
            return "UI スレッド"
        case EventLoopDomain():
            return "イベントループ"
        case NonisolatedDomain():
            return "隔離されない領域"
        case QueueDomain():
            return "キュー"


def _label(domain: Isolation) -> str:
    match domain:
        case ActorDomain(type_name=name) | GlobalActorDomain(name=name):
            return name
        case UiThreadDomain():
            return "UI スレッド"
        case EventLoopDomain():
            return "イベントループ"
        case NonisolatedDomain():
            return "隔離されない"
        case QueueDomain(expression=expression):
            return expression


def _domain_of(adapter: ConcurrencyAdapter, index: Index, member: Member) -> Isolation:
    source = index.trees[member.path].root_node.text or b""
    return adapter.isolation_of(member.node, source, index)


@dataclass
class _Drawing:
    """一関数の図を組む間の状態。列の並びは、領域が最初に現れた順。"""

    adapter: ConcurrencyAdapter
    index: Index
    resolve: Callable[..., Call]
    receiver_type: Callable[..., TypeInfo | str | None]
    # 関数を含むファイルの根。受け手の型の綴りが書かれた場所で、型の名前を import で確かめる
    scope: Node
    columns: dict[Isolation, str] = field(default_factory=dict)
    # 札を付けた呼び先。同じ関数の同じ呼び先の札を一つにまとめる
    flagged: set[str] = field(default_factory=set)
    # 共有の札を付けた (呼び先、引数の型)。同じ組の札を一つにまとめる
    shared: set[tuple[str, str]] = field(default_factory=set)

    def alias(self, domain: Isolation) -> str:
        return self.columns.setdefault(domain, f"D{len(self.columns)}")

    def is_ui_type(self, typed: TypeInfo | str | None) -> bool:
        """受け手の型が UI の型か。判定の規則は言語アダプタが持ち、ここは型の名前を渡すだけ。"""
        if typed is None:
            return False
        name = typed if isinstance(typed, str) else typed.name
        return self.adapter.is_ui_type(name, self.index, self.scope)

    def callee_domain(self, step: AwaitCall | SyncCall) -> Isolation | None:
        callee = resolved_member(self.resolve(step.call))
        return None if callee is None else _domain_of(self.adapter, self.index, callee)

    def ui_update(self, step: SyncCall | MemberAssignment, current: Isolation) -> list[str]:
        """主スレッドの外から UI を更新する、await の無い呼び出しと代入の札。矢印は描かない。"""
        if self.adapter.is_main_thread(current):
            return []
        match step:
            case SyncCall():
                target = self.callee_domain(step)
                receiver = step.call.receiver
                ui = (
                    receiver is not None
                    and not step.call.is_creation
                    and self.is_ui_type(self.receiver_type(receiver))
                )
                into_main = target is not None and self.adapter.is_main_thread(target)
                if not into_main and not ui:
                    return []
                label = step.call.name
            case MemberAssignment():
                if not self.is_ui_type(self.receiver_type(step.receiver)):
                    return []
                label = f"{(step.receiver.text or b'').decode()}.{step.member}"
        if label in self.flagged:
            return []
        self.flagged.add(label)
        return [f"Note over {self.alias(current)}: {_UI_NOTE} {mermaid_text(label)}"]

    def sharing_notes(self, step: AwaitCall, current: Isolation) -> list[str]:
        """領域をまたぐ await の引数に、共有の札を付ける。型の宣言から確かめられた引数だけ。"""
        target = self.callee_domain(step)
        if target is None or target == current:
            return []
        notes = []
        for argument in step.call.arguments:
            typed = self.receiver_type(argument.expression)
            if typed is None:
                continue
            name = typed if isinstance(typed, str) else typed.name
            sharing = _SHARING_REASONS.get(self.adapter.sharing_of(name, self.index))
            key = (step.call.name, name)
            if sharing is None or key in self.shared:
                continue
            self.shared.add(key)
            aliases = f"{self.alias(current)},{self.alias(target)}"
            text = mermaid_text(f"{name}（{sharing}参照型）を {step.call.name} へ渡す")
            notes.append(f"Note over {aliases}: {_SHARING_NOTE} {text}")
        return notes

    def call_arrow(
        self, step: AwaitCall | SyncCall, current: Isolation, *, in_branch: bool
    ) -> str | None:
        """領域をまたぐ呼び出しの矢印。枝の中は塊を残すため、同じ領域でも自分への矢印にする。"""
        target = self.callee_domain(step)
        name = mermaid_text(step.call.name)
        if target is not None and target != current:
            return f"{self.alias(current)}->>{self.alias(target)}: {name}"
        if in_branch:
            # 呼び先を解決できないときも、領域は推測せず枝の本体の領域に置く
            return f"{self.alias(current)}->>{self.alias(current)}: {name}"
        return None

    def enter(self, body: Body, current: Isolation, label: str, depth: int) -> list[str]:
        """直前の領域と本体の領域が違うときだけ、本体へ渡る矢印。"""
        if body.isolation == current:
            return []
        return [f"{_INDENT * depth}{self.alias(current)}->>{self.alias(body.isolation)}: {label}"]

    def render(
        self,
        steps: tuple[ConcurrencyStep, ...] | list[ConcurrencyStep],
        current: Isolation,
        *,
        in_async: bool,
        depth: int,
        in_branch: bool = False,
    ) -> list[str]:
        """in_branch は、構造化された並行の枝の直下の手順を描くときだけ真。"""
        lines: list[str] = []
        pad = _INDENT * depth
        for step in steps:
            match step:
                case AwaitCall():
                    lines.extend(self._await(step, current, pad, in_branch=in_branch))
                case SyncCall() | MemberAssignment():
                    lines.extend(self._sync(step, current, pad, in_branch=in_branch))
                case StructuredConcurrency():
                    lines.extend(self._par(step, current, in_async=in_async, depth=depth))
                case UnstructuredTask():
                    lines.extend(self._task(step, current, depth))
                case QueueDispatch():
                    lines.extend(self._queue(step, current, depth))
                case ContinueOn():
                    # 以降の手順だけが新しい領域で走る。この render の呼び出しの外へは漏らさない
                    current = step.domain
                    note = mermaid_text(step.reason)
                    lines.append(f"{pad}Note over {self.alias(current)}: {note}")
                case BlockingWait():
                    lines.extend(self._wait(step, current, pad, in_async=in_async))
        return lines

    def _await(
        self, step: AwaitCall, current: Isolation, pad: str, *, in_branch: bool
    ) -> list[str]:
        lines: list[str] = []
        arrow = self.call_arrow(step, current, in_branch=in_branch)
        if arrow is not None:
            lines.append(f"{pad}{arrow}")
        lines.extend(f"{pad}{line}" for line in self.sharing_notes(step, current))
        return lines

    def _sync(
        self, step: SyncCall | MemberAssignment, current: Isolation, pad: str, *, in_branch: bool
    ) -> list[str]:
        lines: list[str] = []
        # await の無い呼び出しは、塊の外では札だけで矢印にしない。枝の中は矢印にする
        if isinstance(step, SyncCall) and in_branch:
            arrow = self.call_arrow(step, current, in_branch=in_branch)
            if arrow is not None:
                lines.append(f"{pad}{arrow}")
        lines.extend(f"{pad}{line}" for line in self.ui_update(step, current))
        return lines

    def _wait(
        self, step: BlockingWait, current: Isolation, pad: str, *, in_async: bool
    ) -> list[str]:
        on_main = self.adapter.is_main_thread(current)
        if not (in_async or on_main):
            # 主スレッドでも協調スレッドでもない待ち合わせは、何も塞がないので描かない
            return []
        blocked = "主スレッドを塞ぐ" if on_main else "協調スレッドを塞ぐ"
        note = f"注意: ブロックする待ち合わせ {mermaid_text(step.name)}（{blocked}）"
        return [f"{pad}Note over {self.alias(current)}: {note}"]

    def _par(
        self, step: StructuredConcurrency, current: Isolation, *, in_async: bool, depth: int
    ) -> list[str]:
        pad = _INDENT * depth
        label = step.branch_label or step.label
        branches = []
        for body in step.branches:
            inner = self.render(
                body.steps, body.isolation, in_async=in_async, depth=depth + 1, in_branch=True
            )
            if inner:
                branches.append([*self.enter(body, current, label, depth + 1), *inner])
        if not branches:
            return []
        title = step.label
        lines = [f"{pad}par {title}"]
        for position, branch in enumerate(branches):
            if position:
                lines.append(f"{pad}and {title}")
            lines.extend(branch)
        lines.append(f"{pad}end")
        return lines

    def _task(self, step: UnstructuredTask, current: Isolation, depth: int) -> list[str]:
        pad = _INDENT * depth
        title = "Task" if step.kind == TaskKind.TASK else "Task.detached"
        inner = self.render(step.body.steps, step.body.isolation, in_async=True, depth=depth + 1)
        lines = []
        if not step.handle_kept:
            lines.append(f"{pad}Note over {self.alias(current)}: {_UNSTRUCTURED_NOTE}")
        if inner:
            enter = self.enter(step.body, current, title, depth + 1)
            lines.extend([f"{pad}opt {title}", *enter, *inner, f"{pad}end"])
        return lines

    def _queue(self, step: QueueDispatch, current: Isolation, depth: int) -> list[str]:
        head = self.enter(step.body, current, step.label, depth)
        return [
            *head,
            *self.render(step.body.steps, step.body.isolation, in_async=False, depth=depth),
        ]


def _mermaid(drawing: _Drawing, home: Isolation, body: list[str]) -> str:
    lines = ["sequenceDiagram"]
    # 枠の順が列の順になるので、関数の領域の枠を先頭に置く
    for label in sorted(_BOXES, key=lambda box: box != _box(home)):
        members = [(d, a) for d, a in drawing.columns.items() if _box(d) == label]
        if not members:
            continue
        lines.append(f"    box {label}")
        lines.extend(
            f"        participant {alias} as {mermaid_text(_label(domain))}"
            for domain, alias in members
        )
        lines.append("    end")
    lines.extend(f"    {line}" for line in body)
    return "\n".join(lines) + "\n"


def _function_panel(adapter: ConcurrencyAdapter, index: Index, member: Member) -> Panel | None:
    declaration = member.declaration
    body = adapter.body_of(member.node)
    if declaration is None or body is None:
        return None
    source = index.trees[member.path].root_node.text or b""
    home = _domain_of(adapter, index, member)
    steps = adapter.concurrency_steps(body, source, home)
    if not steps:
        return None
    drawing = _Drawing(
        adapter,
        index,
        call_resolver(index, member),
        receiver_typer(index, member),
        index.trees[member.path].root_node,
    )
    drawing.alias(home)
    lines = drawing.render(steps, home, in_async=adapter.is_async(member.node), depth=0)
    if not lines:
        return None
    return Panel(
        f"{member.qualified_name}（{member.path}:{member.line}）", _mermaid(drawing, home, lines)
    )


def concurrency_diagram(review: Review) -> MermaidFigure | None:
    """並行処理の図。並行の手順を持つ変更した関数が無ければ None。"""
    root = Path(review.repository_root)
    paths = walk_tree_files(root, root) if review.is_diff else review.paths
    adapters: dict[str, ConcurrencyAdapter] = {}
    for path in paths:
        adapter = registry.adapter_for(path)
        if isinstance(adapter, ConcurrencyAdapter):
            adapters.setdefault(adapter.name, adapter)
    panels: list[tuple[str, int, Panel]] = []
    for adapter in adapters.values():
        # 索引と変更した宣言の集め方は、シーケンス図と同じ関数を使う
        index = build_index(root, paths, adapter)
        changed = changed_members(index, review.map_kind.segments, adapter, include_tests=False)
        for member in changed.values():
            panel = _function_panel(adapter, index, member)
            if panel is not None:
                panels.append((member.path, member.line, panel))
    if not panels:
        return None
    ordered = tuple(panel for _, _, panel in sorted(panels, key=lambda p: (p[0], p[1])))
    return MermaidFigure(LEGEND, ordered)
