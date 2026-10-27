"""根ごとのシーケンス図のモデルを組み、JSON と Mermaid の文面へ書き出す（4.2.3〜4.2.6 節）。"""

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
    Side,
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
# 参加者の名札に並べる判断の箇所の数。ツリー図の札（TAG_CAP）と同じ 3 件にして、
# 名札が参加者の名前より長くならない幅に収める
PARTICIPANT_TAG_CAP = 3


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
    """型を解けなかった印。型ではない。"""


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
    root: str
    path: str
    line: int
    labels: list[str]
    participants: list[Participant] = field(default_factory=list)
    arrows: list[Arrow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: EntryEvidence = field(default_factory=EntryEvidence)


class RegionLabels:
    """判断の箇所を、メソッドの宣言（矢印）、型の宣言と変数の宣言（参加者）へ対応付ける。"""

    def __init__(self, index: Index, regions: Sequence[Region]) -> None:
        self.regions = list(regions)
        self.method: dict[Key, str] = {}
        self.participant: dict[TypeKey, list[str]] = {}
        for region in (r for r in self.regions if r.qualified_name):
            self._label_named(index, region)
        # 機能レビュー式の箇所はファイル単位で修飾名を持たない。
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
    ) -> None:
        self.index = index
        self.changed = selection.changed
        self.labels = labels
        self.reach = reach
        self.max_depth = depth
        self.full = full

    def build(self, root: Root) -> Diagram:
        diagram = self._build(root, draw_externals=False)
        if diagram.arrows:
            return diagram
        # 外部の呼び出しを畳んだ形で矢印が 0 本になる入口（外部の型だけを呼ぶ）に限り、
        # 外部の矢印で描き直す。矢印を持つ図に外部の矢印を足すと既存の図の雑音になる
        return self._build(root, draw_externals=True)

    def _build(self, root: Root, *, draw_externals: bool) -> Diagram:
        self.draw_externals = draw_externals
        self.folded = False
        member = root.member
        diagram = Diagram(member.qualified_name, member.path, member.line, list(root.labels))
        self.diagram = diagram
        self.collapsed = 0
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
            diagram.notes.append(f"変更に関わらない呼び出し {self.collapsed} 件")
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
        created = Participant(
            f"P{len(self.diagram.participants)}",
            name,
            _display(self.index.adapter, name),
            identity,
        )
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
                if not (self.full or key_of(target) in self.changed or self.reach.reaches(target)):
                    self.collapsed += 1
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
                if drawn and not recursion:
                    self._walk(target, dict(call.bindings), next_depth, stack | {key_of(target)})
            elif isinstance(resolution, External) and self.draw_externals:
                self._external_arrow(sender, resolution.type, call, depth + 1)
            elif not self.full:
                self.collapsed += 1
            elif isinstance(resolution, Resolved):
                self._arrow(
                    sender,
                    self.index.type_name(resolution.type),
                    IndexedParticipant(resolution.type),
                    call,
                    depth + 1,
                    status="unresolved",
                    region=None,
                )
            else:
                self._arrow(
                    sender,
                    UNRESOLVED_PARTICIPANT,
                    UnresolvedParticipant(),
                    call,
                    depth + 1,
                    status="unresolved",
                    region=None,
                )


def build_diagrams(
    index: Index,
    selection: RootSelection,
    regions: Sequence[Region],
    depth: int = DEFAULT_DEPTH,
    *,
    full: bool = False,
    roots: Sequence[Root] | None = None,
) -> dict[str, Any]:
    """根ごとの図と、J の番号の逆引きと、どの図にも現れない判断の箇所を持つモデルを返す。"""
    chosen = list(selection.roots if roots is None else roots)
    labels = RegionLabels(index, regions)
    reach = Reachability(index, selection.changed, [r.member for r in chosen])
    builder = _Builder(index, selection, labels, reach, depth, full=full)
    diagrams = [builder.build(root) for root in chosen]
    reverse: dict[str, list[dict[str, Any]]] = {}
    for diagram in diagrams:
        for position, arrow in enumerate(diagram.arrows, start=1):
            if arrow.region is not None:
                reverse.setdefault(arrow.region, []).append(
                    {"diagram": diagram.root, "arrow": position, "participant": None}
                )
        for participant in diagram.participants:
            for region_id in participant.regions:
                reverse.setdefault(region_id, []).append(
                    {
                        "diagram": diagram.root,
                        "arrow": None,
                        "participant": participant.qualified_name,
                    }
                )
    unreachable = [
        {
            "id": required(region.id),
            "path": region.path,
            "qualified_name": region.qualified_name,
            "summary": region.summary,
        }
        for region in regions
        if required(region.id) not in reverse
    ]
    return {
        "side": Side.AFTER,
        "diagrams": [diagram_to_dict(d) for d in diagrams],
        "region_index": reverse,
        "unreachable_regions": unreachable,
        "dropped": [m.qualified_name for m in selection.dropped],
    }


def diagram_to_dict(diagram: Diagram) -> dict[str, Any]:
    return {
        "root": diagram.root,
        "path": diagram.path,
        "line": diagram.line,
        "labels": diagram.labels,
        "participants": [
            {
                "alias": p.alias,
                "qualified_name": p.qualified_name,
                "display": p.display,
                "external": p.external,
                "regions": p.regions,
            }
            for p in diagram.participants
        ],
        "arrows": [
            {
                "sender": a.sender,
                "receiver": a.receiver,
                "name": a.name,
                "depth": a.depth,
                "path": a.path,
                "line": a.line,
                "status": a.status,
                "region": a.region,
            }
            for a in diagram.arrows
        ],
        "notes": diagram.notes,
        **diagram.evidence.to_dict(),
    }


def _arrow_signature(arrow: Mapping[str, Any]) -> tuple[str, str, str]:
    return arrow["sender"], arrow["receiver"], arrow["name"]


def mark_differences(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    """変更前と変更後の矢印の列を最長共通部分列で突き合わせ、対比の印 mark を付ける（4.2.4 項）。

    組は（送り手、受け手、呼び出しの名前）で、行番号は使わない。
    """
    old = [_arrow_signature(a) for a in before]
    new = [_arrow_signature(a) for a in after]
    # lengths[i][j] は old[i:] と new[j:] の最長共通部分列の長さ
    lengths = [[0] * (len(new) + 1) for _ in range(len(old) + 1)]
    for i in range(len(old) - 1, -1, -1):
        for j in range(len(new) - 1, -1, -1):
            lengths[i][j] = (
                lengths[i + 1][j + 1] + 1
                if old[i] == new[j]
                else max(lengths[i + 1][j], lengths[i][j + 1])
            )
    for arrow in before:
        arrow["mark"] = "removed"
    for arrow in after:
        arrow["mark"] = "added"
    i = j = 0
    while i < len(old) and j < len(new):
        if old[i] == new[j]:
            before[i]["mark"] = after[j]["mark"] = "same"
            i += 1
            j += 1
        elif lengths[i + 1][j] >= lengths[i][j + 1]:
            i += 1
        else:
            j += 1


MARK_SUFFIX = {"added": "（追加）", "removed": "（削除）"}


def mermaid_text(value: str) -> str:
    return value.replace(";", ",").replace("#", "").replace("\n", " ")


def region_ids(diagram: Mapping[str, Any]) -> list[str]:
    """図の一枚に札として載る判断の箇所の番号。`to_mermaid` が書く順で、重複は除く。"""
    found = [r for p in diagram["participants"] for r in p["regions"]]
    found += [a["region"] for a in diagram["arrows"] if a["region"]]
    return list(dict.fromkeys(found))


def participant_tag(regions: Sequence[str]) -> str:
    """参加者の名札の判断の箇所。上限を超えたら先頭から上限の数だけ並べ、残りは件数にする。"""
    if len(regions) <= PARTICIPANT_TAG_CAP:
        return "".join(f" [{r}]" for r in regions)
    shown = " ".join(regions[:PARTICIPANT_TAG_CAP])
    return f" [{shown} ほか {len(regions) - PARTICIPANT_TAG_CAP} 件]"


def to_mermaid(diagram: Mapping[str, Any]) -> str:
    """図のモデルの一枚を、決まった規則で Mermaid の文面にする。"""
    alias = {p["qualified_name"]: p["alias"] for p in diagram["participants"]}
    lines = ["sequenceDiagram"]
    lines.extend(
        f"    participant {p['alias']} as {mermaid_text(p['display'])}"
        f"{participant_tag(p['regions'])}"
        for p in diagram["participants"]
    )
    for arrow in diagram["arrows"]:
        operator = "-x" if arrow["status"] == "unresolved" else "->>"
        tag = f" [{arrow['region']}]" if arrow["region"] else ""
        sender, receiver = alias[arrow["sender"]], alias[arrow["receiver"]]
        mark = MARK_SUFFIX.get(arrow.get("mark", "same"), "")
        lines.append(
            f"    {sender}{operator}{receiver}: {mermaid_text(arrow['name'])}(){tag}{mark}"
        )
    first = diagram["participants"][0]["alias"]
    lines.extend(
        f"    Note over {first}: {mermaid_text(note)}"
        for note in [*diagram["labels"], *diagram["notes"]]
    )
    return "\n".join(lines) + "\n"


def select_diagrams(
    model: dict[str, Any], root: str | None = None, region: str | None = None
) -> dict[str, Any]:
    if region is not None:
        hits = {entry["diagram"] for entry in model["region_index"].get(region, [])}
        return {
            **model,
            "diagrams": [d for d in model["diagrams"] if d["root"] in hits],
            "region_index": {k: v for k, v in model["region_index"].items() if k == region},
        }
    if root is not None:
        return {**model, "diagrams": [d for d in model["diagrams"] if d["root"] == root]}
    return model
