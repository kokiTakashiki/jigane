"""根ごとのシーケンス図のモデルを組む（4.2.3〜4.2.6 節）。"""

import re
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jigane.judgment_map.region_table import Region
from jigane.languages.base import SequenceAdapter
from jigane.preconditions.required import required
from jigane.sequence.index import Index, Member, TypeKey
from jigane.sequence.resolve import Call, External, Resolved, Unresolved, extract_calls
from jigane.sequence.roots import (
    Key,
    Root,
    RootSelection,
    calls_with_bindings,
    implementations,
    is_interface_member,
    key_of,
    resolved_member,
)

DEFAULT_DEPTH = 4
MIN_DEPTH = 1
MAX_DEPTH = 8
PARTICIPANT_LIMIT = 16
# 一枚の図の外部の参加者がこの数を超えたら、外部をまとめた一つの参加者に畳む
EXTERNAL_PARTICIPANT_LIMIT = 6
EXTERNAL_FOLDED_PARTICIPANT = "外部"
ARROW_LIMIT = 80
UNRESOLVED_PARTICIPANT = "?"
# 呼び先を解けない呼び出しを、ソースの呼びの式の名前の参加者で描くときの接頭辞と表示の添え字
UNRESOLVED_TARGET_PREFIX = "?"
UNRESOLVED_TARGET_SUFFIX = "（解けない呼び先）"
# 一枚の図で、呼びの式ごとの解けない呼び先の参加者を置く上限。残りは件数の注記にする
UNRESOLVED_TARGET_LIMIT = 5
UNRESOLVED_EXPRESSION_WIDTH = 40
# 「変更に関わらない呼び出し」の注記に並べる呼び先の名前の上限。注記を一行に収めるため
COLLAPSED_NAME_LIMIT = 5
_HEAD_NAME = re.compile(r"[^\W\d]\w*")


@dataclass(frozen=True)
class IndexedParticipant:
    """索引にある型。"""

    key: TypeKey


@dataclass(frozen=True)
class ExternalParticipant:
    """索引に無い外部の型。索引に無いので TypeKey は無く、型の名前が同一性になる。"""

    type_name: str


@dataclass(frozen=True)
class FoldedExternals:
    """畳んだ外部の型の束。"""


@dataclass(frozen=True)
class UnresolvedParticipant:
    """型を解けなかった印。型ではない。expression は呼び先の原文で、無ければ印だけの "?"。"""

    expression: str = ""


ParticipantIdentity = (
    IndexedParticipant | ExternalParticipant | FoldedExternals | UnresolvedParticipant
)


@dataclass
class Participant:
    alias: str
    qualified_name: str
    display: str
    identity: ParticipantIdentity
    regions: list[str] = field(default_factory=list)

    @property
    def key(self) -> TypeKey | None:
        return self.identity.key if isinstance(self.identity, IndexedParticipant) else None

    @property
    def external(self) -> bool:
        return not isinstance(self.identity, IndexedParticipant)


@dataclass
class Arrow:
    sender: str
    receiver: str
    name: str
    depth: int
    path: str
    line: int
    status: str
    region: str | None


class EmptyEntryKind(Enum):
    """矢印の無い入口の区分。注記の文面はこの区分ごとに決まる。"""

    NO_CALL = "no_call"
    MARKUP_CALLED = "markup_called"
    UNRELATED_CALL = "unrelated_call"
    UNRESOLVED_CALL = "unresolved_call"


@dataclass
class EntryEvidence:
    """矢印の無い入口に「呼び出しが無い」と言えるかを決める、入口ごとの証拠。"""

    # 受け手の型を解けなかった呼び出しの数（辿った先の本体の分も含む）
    unresolved_calls: int = 0
    # 変更に関わらないとして矢印にしなかった呼び出しの数
    collapsed_calls: int = 0
    # 対になるマークアップ（XAML）のイベント結線が、この入口を呼ぶ
    markup_called: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "unresolved_calls": self.unresolved_calls,
            "collapsed_calls": self.collapsed_calls,
            "markup_called": self.markup_called,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EntryEvidence":
        return cls(
            data.get("unresolved_calls", 0),
            data.get("collapsed_calls", 0),
            data.get("markup_called", False),
        )

    def kind(self) -> EmptyEntryKind:
        """区分を決める唯一の場所。解けない呼び出しは、辿れなかっただけかもしれないので優先する。"""
        if self.unresolved_calls:
            return EmptyEntryKind.UNRESOLVED_CALL
        if self.collapsed_calls:
            return EmptyEntryKind.UNRELATED_CALL
        if self.markup_called:
            return EmptyEntryKind.MARKUP_CALLED
        return EmptyEntryKind.NO_CALL


@dataclass
class Diagram:
    root_member: Member
    labels: list[str]
    participants: list[Participant] = field(default_factory=list)
    arrows: list[Arrow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: EntryEvidence = field(default_factory=EntryEvidence)

    @property
    def root(self) -> str:
        return self.root_member.qualified_name

    @property
    def path(self) -> str:
        return self.root_member.path

    @property
    def line(self) -> int:
        return self.root_member.line


class RegionLabels:
    """判断の箇所を、メソッドの宣言（矢印）、型の宣言と変数の宣言（参加者）へ対応付ける。"""

    def __init__(self, index: Index, regions: Sequence[Region]) -> None:
        self.regions = list(regions)
        self.method: dict[Key, str] = {}
        self.participant: dict[TypeKey, list[str]] = {}
        for region in (r for r in self.regions if r.qualified_name):
            self._label_named(index, region)
        # ファイルのレビューの箇所はファイル単位で修飾名を持たない。
        # 名前のある箇所を先に対応付け、空きに限ってパスで当てる
        for region in self.regions:
            if region.qualified_name:
                continue
            self._label_by_path(index, region)

    def _label_named(self, index: Index, region: Region) -> None:
        """修飾名を持つ箇所を、メソッドの宣言か、型の宣言（参加者）へ対応付ける。"""
        region_id = required(region.id)
        member = self._member(index, region)
        if member is not None:
            self.method.setdefault(key_of(member), region_id)
        else:
            # Python では型の修飾名がモジュール付きなので、ファイルの相対名から作り直して引く
            named = index.declared_type(region.path, region.qualified_name)
            owner = index.declared_type(region.path, region.qualified_name.rpartition(".")[0])
            if named in index.types:
                self.participant.setdefault(named, []).append(region_id)
            elif region.qualified_name.rpartition(".")[0] and owner in index.types:
                self.participant.setdefault(owner, []).append(region_id)

    def _label_by_path(self, index: Index, region: Region) -> None:
        """修飾名を持たない箇所を、同じパスのメソッドの宣言と、空いている型の宣言へ対応付ける。"""
        region_id = required(region.id)
        for member in index.callables.values():
            if member.path == region.path:
                self.method.setdefault(key_of(member), region_id)
        for info in index.types.values():
            if info.path == region.path and info.key not in self.participant:
                self.participant[info.key] = [region_id]

    @staticmethod
    def _member(index: Index, region: Region) -> Member | None:
        span = region.new_span
        candidates = [
            m
            for m in index.callables.values()
            if m.path == region.path and index.declaration_name(m) == region.qualified_name
        ]
        if not candidates:
            return None
        if span is not None:
            for member in candidates:
                declaration = member.declaration
                if (
                    declaration is not None
                    and declaration.span.start_line <= span.end_line
                    and span.start_line <= declaration.span.end_line
                ):
                    return member
        return candidates[0]


class Reachability:
    """索引の中の解決済みの呼び出しの上で、C の要素へ到達できる宣言の集合。"""

    def __init__(
        self, index: Index, changed: Mapping[Key, Member], starts: Sequence[Member]
    ) -> None:
        forward = self._forward_edges(index, starts)
        backward: dict[Key, set[Key]] = {}
        for source, targets in forward.items():
            for target_key in targets:
                backward.setdefault(target_key, set()).add(source)
        self.reaching: set[Key] = set()
        pending = deque(changed)
        while pending:
            current = pending.popleft()
            for source in backward.get(current, ()):
                if source not in self.reaching:
                    self.reaching.add(source)
                    pending.append(source)

    @staticmethod
    def _forward_edges(index: Index, starts: Sequence[Member]) -> dict[Key, set[Key]]:
        """起点から辿れる宣言ごとに、解決済みの呼び出しの先（インターフェースは実装も）を集める。"""
        forward: dict[Key, set[Key]] = {}
        queue = deque(starts)
        seen = {key_of(m) for m in starts}
        while queue:
            member = queue.popleft()
            targets: set[Key] = set()
            for call in calls_with_bindings(index, member, {}):
                callee = resolved_member(call)
                if callee is None:
                    continue
                reached = [callee]
                if is_interface_member(index, callee):
                    reached.extend(implementations(index, callee))
                for target in reached:
                    targets.add(key_of(target))
                    if key_of(target) not in seen:
                        seen.add(key_of(target))
                        queue.append(target)
            forward[key_of(member)] = targets
        return forward

    def reaches(self, member: Member) -> bool:
        return key_of(member) in self.reaching


def _display(adapter: SequenceAdapter, qualified: str) -> str:
    """参加者の表示名。無名クラスはアダプタが決める（Mermaid の別名に山括弧を置けないため）。"""
    return adapter.anonymous_class_display(qualified) or qualified.rsplit(".", 1)[-1]


class _Builder:
    def __init__(
        self,
        index: Index,
        selection: RootSelection,
        labels: RegionLabels,
        reach: Reachability,
        depth: int,
        *,
        full: bool,
        relations: bool,
    ) -> None:
        self.relations = relations
        self.index = index
        self.changed = selection.changed
        self.labels = labels
        self.reach = reach
        self.max_depth = depth
        self.full = full

    def build(self, root: Root) -> Diagram:
        diagram = self._build(root, draw_externals=False, draw_relations=False)
        if diagram.arrows:
            return diagram
        # 変更に関わる呼び出しだけに畳んだ形で矢印が 0 本になる入口（外部の型だけを呼ぶ、
        # 単独の関数、呼び先を解けない）に限り、入口の直接の呼び出しを全て矢印で描き直す。
        # 矢印を持つ図にこれらを足すと既存の図の雑音になる。
        # 変更前の図は「変更した宣言へ届くか」を比べるので、関係の矢印を足さない（relations が偽）
        return self._build(root, draw_externals=True, draw_relations=self.relations)

    def _build(self, root: Root, *, draw_externals: bool, draw_relations: bool) -> Diagram:
        self.draw_externals = draw_externals
        self.draw_relations = draw_relations
        self.unresolved_targets: set[str] = set()
        self.beyond_unresolved_targets = 0
        self.folded = False
        member = root.member
        diagram = Diagram(member, list(root.labels))
        self.diagram = diagram
        self.collapsed = 0
        self.collapsed_repository_calls = 0
        self.collapsed_names: list[str] = []
        self.beyond_depth = 0
        self.beyond_arrows = 0
        self.beyond_participants = 0
        self._participant(self.index.type_name(member.owner), IndexedParticipant(member.owner))
        self._walk(member, {}, 0, {key_of(member)})
        diagram.evidence.collapsed_calls = self.collapsed
        diagram.evidence.markup_called = member.name in self.index.markup_called.get(
            member.path, frozenset()
        )
        if self.collapsed:
            diagram.notes.append(self._collapsed_note())
        if self.beyond_unresolved_targets:
            diagram.notes.append(
                f"解けない呼び先の上限 {UNRESOLVED_TARGET_LIMIT} を超える呼び出し "
                f"{self.beyond_unresolved_targets} 件"
            )
        if self.beyond_depth:
            diagram.notes.append(
                f"深さの上限 {self.max_depth} を超える呼び出し {self.beyond_depth} 件"
            )
        if self.beyond_arrows:
            diagram.notes.append(
                f"矢印の上限 {ARROW_LIMIT} を超える呼び出し {self.beyond_arrows} 件"
            )
        if self.beyond_participants:
            diagram.notes.append(
                f"参加者の上限 {PARTICIPANT_LIMIT} を超える呼び出し {self.beyond_participants} 件"
            )
        for participant in diagram.participants:
            if participant.key is not None:
                participant.regions = list(self.labels.participant.get(participant.key, []))
        return diagram

    def _external_names(self) -> set[str]:
        """畳む対象の外部の型の名前。"""
        # 未解決の "?" は型ではなく、型を解けなかった印なので畳む対象に数えない
        return {
            p.identity.type_name
            for p in self.diagram.participants
            if isinstance(p.identity, ExternalParticipant)
        }

    def _fold_externals(self) -> None:
        """外部の型の参加者を一つに畳み、矢印の向きを付け替える。"""
        diagram = self.diagram
        externals = self._external_names()
        kept = [
            p
            for p in diagram.participants
            if not (
                isinstance(p.identity, ExternalParticipant) and p.identity.type_name in externals
            )
        ]
        if not any(isinstance(p.identity, FoldedExternals) for p in kept):
            kept.append(
                Participant(
                    "",
                    EXTERNAL_FOLDED_PARTICIPANT,
                    EXTERNAL_FOLDED_PARTICIPANT,
                    FoldedExternals(),
                )
            )
        for position, participant in enumerate(kept):
            participant.alias = f"P{position}"
        diagram.participants = kept
        for arrow in diagram.arrows:
            if arrow.receiver in externals:
                arrow.receiver = EXTERNAL_FOLDED_PARTICIPANT
        self.folded = True

    def _external_arrow(self, sender: str, receiver: str, call: Call, depth: int) -> None:
        """外部の型への矢印。参加者の上限で矢印が落ちる前に、外部が多ければ畳む。"""
        known = self._external_names()
        if not self.folded and receiver not in known and len(known) >= EXTERNAL_PARTICIPANT_LIMIT:
            self._fold_externals()
        if self.folded:
            receiver = EXTERNAL_FOLDED_PARTICIPANT
        identity = FoldedExternals() if self.folded else ExternalParticipant(receiver)
        self._arrow(
            sender,
            receiver,
            identity,
            call,
            depth,
            status="external",
            region=None,
        )

    def _participant(self, name: str, identity: ParticipantIdentity) -> Participant | None:
        for existing in self.diagram.participants:
            if existing.identity == identity:
                return existing
        if len(self.diagram.participants) >= PARTICIPANT_LIMIT:
            return None
        if isinstance(identity, UnresolvedParticipant) and identity.expression:
            # 呼びの式は "." を含みうるので、型の名前のように末尾で切らない
            display = f"{identity.expression}{UNRESOLVED_TARGET_SUFFIX}"
        else:
            display = _display(self.index.adapter, name)
        created = Participant(f"P{len(self.diagram.participants)}", name, display, identity)
        self.diagram.participants.append(created)
        return created

    def _dispatch(self, callee: Member) -> Member:
        if not is_interface_member(self.index, callee):
            return callee
        changed = [m for m in implementations(self.index, callee) if key_of(m) in self.changed]
        return changed[0] if len(changed) == 1 else callee

    def _arrow(
        self,
        sender: str,
        receiver: str,
        receiver_identity: ParticipantIdentity,
        call: Call,
        depth: int,
        *,
        status: str,
        region: str | None,
    ) -> bool:
        if len(self.diagram.arrows) >= ARROW_LIMIT:
            self.beyond_arrows += 1
            return False
        if self._participant(receiver, receiver_identity) is None:
            self.beyond_participants += 1
            return False
        self.diagram.arrows.append(
            Arrow(sender, receiver, call.name, depth, call.path, call.line, status, region)
        )
        return True

    def _walk(
        self, member: Member, bindings: Mapping[str, TypeKey], depth: int, stack: set[Key]
    ) -> None:
        sender = self.index.type_name(member.owner)
        for call in extract_calls(self.index, member, bindings).calls:
            resolution = call.resolution
            callee = resolved_member(call)
            if isinstance(resolution, Unresolved):
                self.diagram.evidence.unresolved_calls += 1
            if callee is not None:
                target = self._dispatch(callee)
                related = self.full or key_of(target) in self.changed or self.reach.reaches(target)
                if not related and not self.draw_relations:
                    self._collapse(call.name)
                    continue
                target_owner = self.index.type_name(target.owner)
                next_depth = depth + (target_owner != sender)
                if next_depth > self.max_depth:
                    self.beyond_depth += 1
                    continue
                region = self.labels.method.get(key_of(target))
                drawn = self._arrow(
                    sender,
                    target_owner,
                    IndexedParticipant(target.owner),
                    call,
                    next_depth,
                    status="resolved",
                    region=region,
                )
                recursion = key_of(target) in stack
                # 変更に関わらない呼び先は、入口からの関係を示すだけで本体は辿らない
                if drawn and not recursion and related:
                    self._walk(target, dict(call.bindings), next_depth, stack | {key_of(target)})
            else:
                self._walk_unmatched(sender, call, depth + 1)

    def _walk_unmatched(self, sender: str, call: Call, depth: int) -> None:
        """呼び先の宣言を見つけられなかった呼び出し。"""
        resolution = call.resolution
        if isinstance(resolution, External) and self.draw_externals:
            self._external_arrow(sender, resolution.type, call, depth)
        elif isinstance(resolution, Resolved) and self.full:
            self._arrow(
                sender,
                self.index.type_name(resolution.type),
                IndexedParticipant(resolution.type),
                call,
                depth,
                status="unresolved",
                region=None,
            )
        elif (
            isinstance(resolution, Unresolved)
            and self.draw_relations
            and self._names_repository_module(call, resolution)
        ):
            self._unresolved_target_arrow(sender, resolution, call, depth)
        elif self.full:
            self._arrow(
                sender,
                UNRESOLVED_PARTICIPANT,
                UnresolvedParticipant(),
                call,
                depth,
                status="unresolved",
                region=None,
            )
        elif isinstance(resolution, Unresolved) and self._names_repository_module(
            call, resolution
        ):
            # 関係の矢印にするのはリポジトリの中のモジュールを呼ぶ呼び出しだけ。
            # 件数に数えるときは、注記に名前を残す
            self._collapse(call.name)
        else:
            # 宣言がリポジトリに無い呼び出し（標準ライブラリ、外の依存、組み込み）は
            # 「変更に関わらない呼び出し」の件数にだけ数え、名前は並べない
            self.collapsed += 1

    def _collapse(self, name: str) -> None:
        """リポジトリの中の呼び出しを、名前を残して「変更に関わらない呼び出し」に数える。"""
        self.collapsed += 1
        self.collapsed_repository_calls += 1
        if name not in self.collapsed_names:
            self.collapsed_names.append(name)

    def _collapsed_note(self) -> str:
        note = f"変更に関わらない呼び出し {self.collapsed} 件"
        if not self.collapsed_names:
            return note
        shown = self.collapsed_names[:COLLAPSED_NAME_LIMIT]
        rest = len(self.collapsed_names) - len(shown)
        if rest:
            shown.append(f"ほか {rest} 件")
        external = self.collapsed - self.collapsed_repository_calls
        tail = f"（ほか外の呼び出し {external} 件）" if external else ""
        return f"{note}: {'、'.join(shown)}{tail}"

    def _names_repository_module(self, call: Call, resolution: Unresolved) -> bool:
        """呼びの式の先頭の名前が、そのファイルの import でリポジトリの中のモジュールを指すか。"""
        head = _HEAD_NAME.match(resolution.text)
        if head is None:
            return False
        origin = self.index.imported.get(call.path, {}).get(head.group())
        if origin is None:
            return False
        return any(
            origin == module or origin.startswith(f"{module}.") or module.startswith(f"{origin}.")
            for module in self.index.modules.values()
        )

    def _unresolved_target_arrow(
        self, sender: str, resolution: Unresolved, call: Call, depth: int
    ) -> None:
        """呼び先を解けない呼び出しを、ソースの呼びの式を名前にした参加者への矢印で描く。"""
        text = " ".join(resolution.text.split())[:UNRESOLVED_EXPRESSION_WIDTH]
        expression = f"{text}.{call.name}" if text else call.name
        if (
            expression not in self.unresolved_targets
            and len(self.unresolved_targets) >= UNRESOLVED_TARGET_LIMIT
        ):
            self.beyond_unresolved_targets += 1
            return
        drawn = self._arrow(
            sender,
            f"{UNRESOLVED_TARGET_PREFIX}{expression}",
            UnresolvedParticipant(expression),
            call,
            depth,
            status="unresolved",
            region=None,
        )
        if drawn:
            self.unresolved_targets.add(expression)


@dataclass(frozen=True)
class ArrowHit:
    """判断の箇所が当たる矢印。diagram は図の根の修飾名、arrow はその図の矢印の 1 始まりの位置。"""

    diagram: str
    arrow: int


@dataclass(frozen=True)
class ParticipantHit:
    """判断の箇所が当たる参加者。diagram は図の根の修飾名、participant は参加者の修飾名。"""

    diagram: str
    participant: str


@dataclass(frozen=True)
class UnreachableRegion:
    """どの図の矢印にも参加者にも当たらない判断の箇所。"""

    id: str
    path: str
    qualified_name: str
    summary: str


@dataclass(frozen=True)
class DiagramSetNotes:
    """DiagramSet の図以外の項目。J の番号の逆引き、どの図にも現れない判断の箇所、落とした根。"""

    region_index: Mapping[str, tuple[ArrowHit | ParticipantHit, ...]]
    unreachable_regions: tuple[UnreachableRegion, ...]
    dropped: tuple[str, ...]


@dataclass(frozen=True)
class DiagramSet:
    """根ごとの図と、図以外の項目（DiagramSetNotes）。JSON の辞書にする前の形。"""

    diagrams: tuple[Diagram, ...]
    notes: DiagramSetNotes


@dataclass(frozen=True)
class DiagramInputs:
    """図の構築の入力。索引、選んだ根、判断の箇所、辿る深さ、絞り込みの有無。"""

    index: Index
    selection: RootSelection
    regions: Sequence[Region]
    depth: int = DEFAULT_DEPTH
    full: bool = False


def construct_diagrams(inputs: DiagramInputs, *, relations: bool = True) -> DiagramSet:
    """根ごとの図と、J の番号の逆引きと、どの図にも現れない判断の箇所を作る。

    relations が偽のとき、関係の矢印を足さない。変更前の図は「変更した宣言へ届くか」を比べるため。
    """
    index, selection, regions = inputs.index, inputs.selection, inputs.regions
    depth, full = inputs.depth, inputs.full
    chosen = list(selection.roots)
    labels = RegionLabels(index, regions)
    reach = Reachability(index, selection.changed, [r.member for r in chosen])
    builder = _Builder(index, selection, labels, reach, depth, full=full, relations=relations)
    diagrams = [builder.build(root) for root in chosen]
    reverse: dict[str, list[ArrowHit | ParticipantHit]] = {}
    for diagram in diagrams:
        for position, arrow in enumerate(diagram.arrows, start=1):
            if arrow.region is not None:
                reverse.setdefault(arrow.region, []).append(ArrowHit(diagram.root, position))
        for participant in diagram.participants:
            for region_id in participant.regions:
                reverse.setdefault(region_id, []).append(
                    ParticipantHit(diagram.root, participant.qualified_name)
                )
    unreachable = [
        UnreachableRegion(required(region.id), region.path, region.qualified_name, region.summary)
        for region in regions
        if required(region.id) not in reverse
    ]
    notes = DiagramSetNotes(
        {region: tuple(entries) for region, entries in reverse.items()},
        tuple(unreachable),
        tuple(m.qualified_name for m in selection.dropped),
    )
    return DiagramSet(tuple(diagrams), notes)
