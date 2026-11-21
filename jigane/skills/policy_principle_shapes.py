"""方針の図に、原則が問える図の形があるか。原則ごとの起動の条件の判定。

形は図の文字列でなく、図を作る前の構造の値から読む。どの原則がどの形を要るかの表は
`policy_principles.SHAPES_BY_PRINCIPLE`。
"""

from enum import StrEnum

from jigane.figures.models import SequenceFigure
from jigane.languages import registry
from jigane.policy_materials.classes import by_simple_name
from jigane.policy_materials.type_deltas import (
    _IDENTIFIER,
    TypeDelta,
    TypeDeltas,
    members_of,
    type_deltas,
)
from jigane.sequence.index import Member, TypeInfo, TypeKey
from jigane.sequence.indexes import Indexes
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import REGISTRY, build_figure

WIDE_SIGNATURE_PARAMETERS = 3


class Shape(StrEnum):
    VALUE_SETS = "value_sets"
    FIELD_COPIES = "field_copies"
    CALLERS = "callers"
    DISCRIMINATORS = "discriminators"
    STORES = "stores"
    SEQUENCE = "sequence"
    SINGLE_IMPLEMENTATION = "single_implementation"
    INHERITANCE = "inheritance"
    PRIVATE_TYPE = "private_type"
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


def shared_type_deltas(assembly: AssemblyInput) -> TypeDeltas:
    return assembly.remember("type_deltas", lambda: type_deltas(assembly.review))


def _is_private_name(name: str) -> bool:
    return (
        len(name) > 1
        and name.startswith("_")
        and not (name.startswith("__") and name.endswith("__"))
    )


def private_type_members(result: TypeDeltas) -> tuple[tuple[Member, tuple[str, ...]], ...]:
    """クラス図に出る Python の公開項目から、内部名の型注記とその位置を拾う。"""
    found = []
    adapters = registry.adapters()
    for delta in result.deltas:
        info = delta.info
        adapter = registry.adapter_for(info.path)
        if (
            adapter is None
            or adapter.name != "python"
            or registry.is_test_path(info.path, adapters)
        ):
            continue
        if info.kind != "module" and any(
            _is_private_name(name) for name in result.indexes.display(info).split(".")
        ):
            continue
        for member in members_of(info):
            if _is_private_name(member.name):
                continue
            annotations = [member.type_name or "", *(p.type_name for p in member.parameters)]
            names = {
                name
                for annotation in annotations
                for name in _IDENTIFIER.findall(annotation)
                if _is_private_name(name)
            }
            if names:
                found.append((member, tuple(sorted(names))))
    return tuple(found)


def information_hiding_shapes(assembly: AssemblyInput) -> frozenset[Shape]:
    """Codex の方針で P5 を問える図の形。"""
    found = {
        Shape.STORES
        for figure in REGISTRY
        if figure.key == Shape.STORES.value and build_figure(figure, assembly) is not None
    }
    if private_type_members(shared_type_deltas(assembly)):
        found.add(Shape.PRIVATE_TYPE)
    return frozenset(found)


def present_shapes(assembly: AssemblyInput, sequence: SequenceFigure) -> frozenset[Shape]:
    """レビューの図にある形。重い図は一度だけ読む。sequence は節の組み立てで作った値。"""
    found = {
        shape
        for figure in REGISTRY
        for shape in TABLE_SHAPES
        if figure.key == shape.value and build_figure(figure, assembly) is not None
    }
    if sequence.flows:
        found.add(Shape.SEQUENCE)
    result = shared_type_deltas(assembly)
    shown = [delta.info for delta in result.deltas]
    edges = _edges(shown)
    if edges:
        found.add(Shape.INHERITANCE)
    if _has_single_implementation(edges):
        found.add(Shape.SINGLE_IMPLEMENTATION)
    if _has_wide_signature(result.deltas, result.before_indexes):
        found.add(Shape.WIDE_SIGNATURE)
    return frozenset(found)
