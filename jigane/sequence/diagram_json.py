"""図の並びを、識別子を載せた JSON の辞書へ書き出す。識別子の導出は identifiers が持つ。"""

from collections.abc import Sequence
from typing import Any

from jigane.gates.tree_side import TreeSide
from jigane.sequence.diagram import (
    ArrowHit,
    DiagramSetNotes,
    ParticipantHit,
    UnreachableRegion,
)
from jigane.sequence.identifiers import IdentifiedArrow, IdentifiedDiagram


def _arrow_to_dict(identified: IdentifiedArrow) -> dict[str, Any]:
    arrow = identified.arrow
    return {
        "id": identified.arrow_id,
        "sender": arrow.sender,
        "receiver": arrow.receiver,
        "name": arrow.name,
        "depth": arrow.depth,
        "path": arrow.path,
        "line": arrow.line,
        "status": arrow.status,
        "region": arrow.region,
    }


def diagram_to_dict(identified: IdentifiedDiagram) -> dict[str, Any]:
    """識別子の矢印が図の矢印と食い違うとき ValueError を投げる。"""
    diagram = identified.diagram
    identities = identified.identities
    if [i.arrow for i in identities.arrows] != diagram.arrows:
        raise ValueError(f"識別子の矢印が図の矢印と食い違います: {diagram.root}")
    return {
        "id": identities.diagram_id,
        "root_id": identities.root_id,
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
        "arrows": [_arrow_to_dict(identified) for identified in identities.arrows],
        "notes": diagram.notes,
        **diagram.evidence.to_dict(),
    }


def _hit_to_dict(hit: ArrowHit | ParticipantHit) -> dict[str, Any]:
    if isinstance(hit, ArrowHit):
        return {"diagram": hit.diagram, "arrow": hit.arrow, "participant": None}
    return {"diagram": hit.diagram, "arrow": None, "participant": hit.participant}


def _unreachable_to_dict(region: UnreachableRegion) -> dict[str, Any]:
    return {
        "id": region.id,
        "path": region.path,
        "qualified_name": region.qualified_name,
        "summary": region.summary,
    }


def diagrams_to_dict(
    identified: Sequence[IdentifiedDiagram], notes: DiagramSetNotes, side: TreeSide
) -> dict[str, Any]:
    """識別子付きの図の並びと図以外の項目を、図のモデルの辞書にする。"""
    return {
        "side": side,
        "diagrams": [diagram_to_dict(d) for d in identified],
        "region_index": {k: [_hit_to_dict(h) for h in v] for k, v in notes.region_index.items()},
        "unreachable_regions": [_unreachable_to_dict(r) for r in notes.unreachable_regions],
        "dropped": list(notes.dropped),
    }
