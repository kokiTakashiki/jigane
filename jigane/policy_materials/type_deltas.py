"""変更した型と参照される型の、変更前後の差。クラス図に描く前の構造。

型とメンバの宣言だけから作り、関数の本体は読まない。描く側は `classes.py`。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from jigane.git.tree_files import walk_tree_files
from jigane.judgment_map.classification import Segment
from jigane.languages import registry
from jigane.languages.base import SequenceAdapter
from jigane.policy_materials import changed_files
from jigane.policy_materials.moved_declarations import PlacedDeclaration, find_moved
from jigane.reviews.lifecycle import Review
from jigane.sequence.index import Member, TypeInfo, TypeKey
from jigane.sequence.indexes import Indexes

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def _flat(text: str) -> str:
    # Mermaid の項目の行は、波括弧と山括弧と改行を含められない
    flat = " ".join(text.split())
    return flat.replace("<", "~").replace(">", "~").replace("{", "(").replace("}", ")")


def render_member(member: Member) -> str:
    """項目の一行。名前と型注記の原文。メソッドは仮引数の型と戻り値の型を添える。"""
    if member.kind in ("method", "constructor"):
        parameters = ", ".join(
            f"{p.name}{'?' if p.optional and not p.type_name.endswith('?') else ''}: {p.type_name}"
            if p.type_name
            else p.name
            for p in member.parameters
        )
        returns = f" {member.type_name}" if member.type_name else ""
        # Mermaid の classDiagram は、メソッドの末尾の `*` を抽象として描く
        return _flat(f"{member.name}({parameters}){returns}{'*' if member.abstract else ''}")
    return _flat(f"{member.name}: {member.type_name}" if member.type_name else member.name)


def members_of(info: TypeInfo) -> list[Member]:
    return sorted(
        (m for ms in info.members.values() for m in ms), key=lambda m: (m.path, m.line, m.name)
    )


def _mentioned(info: TypeInfo) -> set[str]:
    """型の項目の型注記とシグネチャと基底に現れる識別子。"""
    names: set[str] = set(info.bases)
    for member in members_of(info):
        annotations = [member.type_name or "", *(p.type_name for p in member.parameters)]
        for annotation in annotations:
            names.update(_IDENTIFIER.findall(annotation))
    return names


def _changed_types(indexes: Indexes, segments: Iterable[Segment]) -> list[TypeInfo]:
    """変更後の側の範囲に宣言か項目が入る型。型の名前が箇所の名前と一致するものも含む。"""
    found: dict[TypeKey, TypeInfo] = {}
    spans = [
        (s.new_path, s.new_span, s.qualified_name)
        for s in segments
        if s.new_path is not None and s.new_span is not None
    ]
    for info in indexes.types():
        members = members_of(info)
        if info.kind == "module" and not members:
            # ファイル全体を表すだけの型で、図に出す外形が無い
            continue
        for path, span, name in spans:
            if path not in info.paths:
                continue
            first, last = span.start_line, span.end_line
            if (
                (name != "" and indexes.declared(path, name) is info)
                or (path == info.path and first <= info.line <= last)
                or any(m.path == path and first <= m.line <= last for m in members)
            ):
                found[info.key] = info
                break
    return sorted(found.values(), key=lambda t: (t.path, t.line))


def _without_unchanged_variables(
    infos: list[TypeInfo], segments: Iterable[Segment]
) -> list[TypeInfo]:
    """モジュール直下の変数のうち、変更後の側の範囲に入らないものを外した型。

    変更していない定数は図の雑音になる。変更した関数のあるモジュールにも、同じファイルの
    変更していない定数は載せない。外した結果、項目が空になったモジュールの型は、外形が
    無いので結果から除く（モジュールの型の行は 1 なので、先頭の import の変更だけで入りうる）。
    """
    spans = [
        (s.new_path, s.new_span)
        for s in segments
        if s.new_path is not None and s.new_span is not None
    ]

    def changed(member: Member) -> bool:
        return any(
            member.path == path and span.start_line <= member.line <= span.end_line
            for path, span in spans
        )

    result: list[TypeInfo] = []
    for info in infos:
        members = {
            name: kept
            for name, group in info.members.items()
            if (kept := tuple(m for m in group if m.kind != "variable" or changed(m)))
        }
        if info.kind == "module" and not members:
            continue
        result.append(replace(info, members=members))
    return result


def referenced_types(indexes: Indexes, changed: list[TypeInfo]) -> list[TypeInfo]:
    """changed の項目の型注記とシグネチャと基底に現れる、索引にある型。一段だけ。"""
    known = {t.key for t in changed}
    found: dict[TypeKey, TypeInfo] = {}
    for info in changed:
        for name in sorted(_mentioned(info)):
            for candidate in indexes.named(name)[:1]:
                if candidate.key not in known:
                    found.setdefault(candidate.key, candidate)
    return sorted(found.values(), key=lambda t: (t.path, t.line))


def _marks(
    after: TypeInfo, name: str, before: TypeInfo | None
) -> tuple[dict[tuple[str, int], str], list[str], list[str]]:
    """項目ごとの印と、削除した項目と、`~` の項目の前の形。before が None なら型そのものが追加。"""
    if before is None:
        return {}, [], []
    before_renders: dict[str, set[str]] = {}
    for member in members_of(before):
        before_renders.setdefault(member.name, set()).add(render_member(member))
    marks: dict[tuple[str, int], str] = {}
    previous: list[str] = []
    after_names = set()
    for member in members_of(after):
        after_names.add(member.name)
        renders = before_renders.get(member.name)
        if renders is None:
            marks[(member.path, member.line)] = "+"
        elif render_member(member) not in renders:
            marks[(member.path, member.line)] = "~"
            # 公開のシグネチャの節を資料から外した代わりに、前の形をここで渡す
            previous.append(f"{name}.{render_member(member)}（前: {' / '.join(sorted(renders))}）")
    removed = [
        f"{name}.{text}"
        for member_name, renders in sorted(before_renders.items())
        if member_name not in after_names
        for text in sorted(renders)
    ]
    return marks, removed, previous


def _shape_of(indexes: Indexes, info: TypeInfo) -> str:
    """型の外形。種類と基底と項目の一行の並びで、宣言の位置を含まない。"""
    members = sorted(render_member(m) for m in members_of(info))
    return " | ".join([info.kind, *info.bases, *members, *indexes.enum_values(info)])


def _moved_origins(
    indexes: Indexes, before_indexes: Indexes, changed: list[TypeInfo]
) -> dict[TypeKey, TypeInfo]:
    """変更後の型の同一性から、移す前の型への対応。変更前に同名の型が無い型だけが候補になる。"""
    added = [
        PlacedDeclaration(t.qualified_name, t.kind, t.path, _shape_of(indexes, t), t)
        for t in changed
        if t.kind != "module" and before_indexes.find(t.key) is None
    ]
    deleted = [
        PlacedDeclaration(t.qualified_name, t.kind, t.path, _shape_of(before_indexes, t), t)
        for t in before_indexes.types()
        if t.kind != "module" and indexes.find(t.key) is None
    ]
    return {pair.new.item.key: pair.old.item for pair in find_moved(deleted, added)}


@dataclass(frozen=True)
class TypeShapeChange:
    """同じ型の変更前後の、基底と種類の差。図の継承の線と型の種類は変更後だけから引くので、ここで残す。"""

    name: str
    bases_before: tuple[str, ...]
    bases_after: tuple[str, ...]
    kind_before: str
    kind_after: str

    @property
    def bases_changed(self) -> bool:
        return set(self.bases_before) != set(self.bases_after)

    @property
    def kind_changed(self) -> bool:
        return self.kind_before != self.kind_after


def type_shape_change(before: TypeInfo, after: TypeInfo) -> TypeShapeChange | None:
    """変更前後の同じ型から、基底（集合として）か種類の差を返す。どちらも同じなら None。"""
    change = TypeShapeChange(after.name, before.bases, after.bases, before.kind, after.kind)
    if not (change.bases_changed or change.kind_changed):
        return None
    return change


@dataclass(frozen=True)
class TypeDelta:
    """一つの型の、変更前後の差。member_marks は組み立てた後に書き換えない。"""

    info: TypeInfo
    changed: bool  # 変更した型なら真、参照されるだけの型なら偽
    added: bool
    moved_from: TypeInfo | None
    member_marks: dict[tuple[str, int], str]  # (パス, 行) → `+` か `~`
    removed_members: tuple[str, ...]
    previous_forms: tuple[str, ...]
    shape: TypeShapeChange | None
    value_marks: tuple[tuple[str, str], ...]  # 列挙の値 → `+` か空
    removed_values: tuple[str, ...] = ()  # removed_members のうち、削除した列挙の値


@dataclass(frozen=True)
class TypeDeltas:
    """図に出す型の差の並びと、それを作った索引。"""

    deltas: tuple[TypeDelta, ...]
    indexes: Indexes
    before_indexes: Indexes | None
    unsupported: tuple[str, ...]


def type_deltas(review: Review) -> TypeDeltas:
    """変更後の木の型を、変更前の木の同じ型と比べる。変更した型の後ろに、参照される型が続く。

    ファイルのレビューは変更後だけで、比べは無い。言語アダプタが無いファイルは unsupported に入る。
    変更した型が無ければ deltas は空。
    """
    root = Path(review.repository_root)
    changed_paths = [p for p, e in changed_files.file_changes(review).items() if e.exists_after]
    unsupported = tuple(
        p for p in changed_paths if not isinstance(registry.adapter_for(p), SequenceAdapter)
    )
    paths = walk_tree_files(root, root) if review.is_diff else review.paths
    indexes = Indexes(root, paths)
    segments = review.map_kind.segments
    changed = _without_unchanged_variables(_changed_types(indexes, segments), segments)
    if not changed:
        return TypeDeltas((), indexes, None, unsupported)
    referenced = _without_unchanged_variables(referenced_types(indexes, changed), segments)
    before_indexes: Indexes | None = None
    if review.base_tree_path is not None:
        before_paths = list(changed_files.file_changes(review, before=True))
        before_indexes = Indexes(review.base_tree_path, before_paths)
    changed_keys = {t.key for t in changed}
    moved = _moved_origins(indexes, before_indexes, changed) if before_indexes is not None else {}
    deltas: list[TypeDelta] = []
    for info in [*changed, *referenced]:
        is_changed = info.key in changed_keys
        marks: dict[tuple[str, int], str] = {}
        added = False
        moved_from = None
        shape = None
        removed: list[str] = []
        removed_values: list[str] = []
        previous: list[str] = []
        shown_name = indexes.display(info)
        after_values = indexes.enum_values(info)
        value_marks = dict.fromkeys(after_values, "")
        if is_changed and before_indexes is not None:
            moved_from = moved.get(info.key)
            before = moved_from or before_indexes.find(info.key)
            added = before is None
            if before is not None:
                shape = type_shape_change(before, info)
            # 外した変数を削除と取り違えないよう、印は外す前の型から作る
            marks, gone, previous = _marks(indexes.find(info.key) or info, shown_name, before)
            removed += gone
            before_values = [] if before is None else before_indexes.enum_values(before)
            value_marks = {v: "" if v in before_values else "+" for v in after_values}
            gone_values = [f"{shown_name}.{v}" for v in before_values if v not in after_values]
            removed += gone_values
            removed_values = gone_values
            if added:
                marks = {(m.path, m.line): "+" for m in members_of(info)}
        deltas.append(
            TypeDelta(
                info,
                is_changed,
                added,
                moved_from,
                marks,
                tuple(removed),
                tuple(previous),
                shape,
                tuple(value_marks.items()),
                tuple(removed_values),
            )
        )
    return TypeDeltas(tuple(deltas), indexes, before_indexes, unsupported)
