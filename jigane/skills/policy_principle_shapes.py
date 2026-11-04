"""方針の図に、原則が問える図の形があるか。原則ごとの起動の条件の判定。

形は図の文字列でなく、図を作る前の構造の値から読む。どの原則がどの形を要るかの表は
`policy_principles.SHAPES_BY_PRINCIPLE`。
"""

from enum import StrEnum

from jigane.figures.models import SequenceFigure
from jigane.policy_materials.classes import by_simple_name
from jigane.policy_materials.type_deltas import TypeDelta, type_deltas
from jigane.reviews.lifecycle import Review
from jigane.sequence.index import Member, TypeInfo, TypeKey
from jigane.sequence.indexes import Indexes
from jigane.skills.policy_figures import REGISTRY

WIDE_SIGNATURE_PARAMETERS = 3


class Shape(StrEnum):
    VALUE_SETS = "value_sets"
    FIELD_COPIES = "field_copies"
    CALLERS = "callers"
    DISCRIMINATORS = "discriminators"
    SEQUENCE = "sequence"
    SINGLE_IMPLEMENTATION = "single_implementation"
    INHERITANCE = "inheritance"
    WIDE_SIGNATURE = "wide_signature"


# 表の図の形は、登録簿の同じ鍵の図が None でないこと
TABLE_SHAPES = (
    Shape.VALUE_SETS,
    Shape.FIELD_COPIES,
    Shape.CALLERS,
    Shape.DISCRIMINATORS,
)


def _is_callable(member: Member) -> bool:
    return member.kind in ("method", "constructor")


def _edges(shown: list[TypeInfo]) -> list[tuple[TypeInfo, TypeInfo]]:
    """図に引かれる継承の辺。(基底, 派生)。`classes.relations` の `<|--` と同じ引き方。"""
    by_simple = by_simple_name(shown)
    return [
        (target, info)
        for info in shown
        for base in info.bases
        for target in by_simple.get(base, [])[:1]
    ]


def _has_single_implementation(edges: list[tuple[TypeInfo, TypeInfo]]) -> bool:
    implementations: dict[TypeKey, int] = {}
    for base, _derived in edges:
        if base.abstract or base.kind == "interface":
            implementations[base.key] = implementations.get(base.key, 0) + 1
    return 1 in implementations.values()


def _added_parameters(delta: TypeDelta, before: TypeInfo | None) -> list[int]:
    """追加か変更の印が付いた関数ごとの、足した引数の数。新しい関数は引数の全部。"""
    added: list[int] = []
    for group in delta.info.members.values():
        for member in group:
            mark = delta.member_marks.get((member.path, member.line), "")
            if not _is_callable(member) or mark == "":
                continue
            previous = [
                len(b.parameters) for b in (before.members.get(member.name, ()) if before else ())
            ]
            if mark == "~" and previous:
                added.append(len(member.parameters) - min(previous))
            else:
                added.append(len(member.parameters))
    return added


def _has_wide_signature(deltas: tuple[TypeDelta, ...], before_indexes: Indexes | None) -> bool:
    for delta in deltas:
        if not delta.changed:
            continue
        before = delta.moved_from
        if before is None and before_indexes is not None:
            before = before_indexes.find(delta.info.key)
        if any(n >= WIDE_SIGNATURE_PARAMETERS for n in _added_parameters(delta, before)):
            return True
    return False


def present_shapes(review: Review, sequence: SequenceFigure) -> frozenset[Shape]:
    """レビューの図にある形。重い図は一度だけ読む。sequence は節の組み立てで作った値。"""
    found = {
        shape
        for figure in REGISTRY
        for shape in TABLE_SHAPES
        if figure.key == shape.value and figure.build(review) is not None
    }
    if sequence.flows:
        found.add(Shape.SEQUENCE)
    result = type_deltas(review)
    shown = [delta.info for delta in result.deltas]
    edges = _edges(shown)
    if edges:
        found.add(Shape.INHERITANCE)
    if _has_single_implementation(edges):
        found.add(Shape.SINGLE_IMPLEMENTATION)
    if _has_wide_signature(result.deltas, result.before_indexes):
        found.add(Shape.WIDE_SIGNATURE)
    return frozenset(found)
