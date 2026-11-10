"""図と矢印の安定した識別子。並びの位置でなく、中身から決まる値を作る。"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from jigane.sequence.diagram import Arrow, Diagram, DiagramSet
from jigane.sequence.index import Member
from jigane.sequence.side import TreeSide

_SEPARATOR = "\x1f"
_DIGEST_LENGTH = 12


@dataclass(frozen=True)
class IdentifiedArrow:
    """矢印と、その矢印の識別子の対。"""

    arrow: Arrow
    arrow_id: str


@dataclass(frozen=True)
class DiagramIdentities:
    """一枚の図の根、図、矢印の識別子。矢印ごとに識別子を結んだ対を、図の矢印の並びと同じ順で持つ。"""

    root_id: str
    diagram_id: str
    arrows: tuple[IdentifiedArrow, ...]


@dataclass(frozen=True)
class IdentifiedDiagram:
    """図と、その図の識別子の対。"""

    diagram: Diagram
    identities: DiagramIdentities


def _digest(prefix: str, *parts: str) -> str:
    joined = _SEPARATOR.join(parts)
    return f"{prefix}_{hashlib.sha256(joined.encode()).hexdigest()[:_DIGEST_LENGTH]}"


def _member_signature(member: Member) -> str:
    """多重定義を区別する形。宣言の種類と、仮引数のラベルと型の並び。"""
    parameters = ",".join(f"{p.label or ''}:{p.type_name}" for p in member.parameters)
    return f"{member.kind}({parameters})"


_ArrowIdentity = tuple[str, str, str, str, int]


def _arrow_identity(arrow: Arrow) -> _ArrowIdentity:
    """矢印の同一性を決める五項目（送り手、受け手、名前、パス、行）。深さと状態は含めない。"""
    return (arrow.sender, arrow.receiver, arrow.name, arrow.path, arrow.line)


def _arrow_ids(diagram_id: str, keys: Sequence[_ArrowIdentity]) -> tuple[str, ...]:
    """矢印の識別子を並びと同じ順に返す。

    同じ図に鍵の同じ矢印が複数あるときは、その中での出現の順（0 始まり）を足して区別する。
    """
    seen: dict[_ArrowIdentity, int] = {}
    ids: list[str] = []
    for key in keys:
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        sender, receiver, name, path, line = key
        ids.append(
            _digest("a", diagram_id, sender, receiver, name, path, str(line), str(occurrence))
        )
    return tuple(ids)


def _identities_of_one(diagram: Diagram, side: TreeSide) -> DiagramIdentities:
    """一枚の図の識別子。根は修飾名とシグネチャから決まり側を含めず、図は根と側から決まる。

    同じ入力から作り直せば同じ値になる。矢印の識別子は呼び出しの行を材料に含めるため、
    呼び出しの行が動けばその矢印の識別子は変わる。
    """
    root = _digest("r", diagram.root, _member_signature(diagram.root_member))
    identifier = _digest("d", root, side.value)
    keys = [_arrow_identity(arrow) for arrow in diagram.arrows]
    ids = _arrow_ids(identifier, keys)
    arrows = tuple(
        IdentifiedArrow(arrow, arrow_id)
        for arrow, arrow_id in zip(diagram.arrows, ids, strict=True)
    )
    return DiagramIdentities(root, identifier, arrows)


def identities_of(diagram_set: DiagramSet, side: TreeSide) -> tuple[IdentifiedDiagram, ...]:
    """図ごとに図とその識別子を対にして並べる。"""
    return tuple(IdentifiedDiagram(d, _identities_of_one(d, side)) for d in diagram_set.diagrams)
