"""方針のレビューに渡すクラス図。変更した型の外形を Mermaid の classDiagram にする。

型とメンバの宣言だけから作り、関数の本体は読まない。図の読み方は LEGEND にある。
"""

import re

from jigane.figures.models import MermaidFigure, Panel
from jigane.policy_materials.type_deltas import (
    _IDENTIFIER,
    TypeShapeChange,
    _flat,
    members_of,
    render_member,
    type_deltas,
)
from jigane.reviews.lifecycle import Review
from jigane.sequence.index import Member, TypeInfo
from jigane.sequence.indexes import Indexes

SECTION_CLASSES = "クラス図"
NO_CLASSES_TEXT = "変更した宣言を持つ型が、索引に無い。"
LEGEND = (
    "変更した宣言を持つ型と、その項目の型注記とシグネチャに現れる型（一段だけ）を、型と項目の宣言から"
    "作った。関数の本体は含まない。型の直前の `%%` の行が、型の宣言のパスと行。"
    "項目の行頭の `+` は変更で追加した項目、`~` は変更で型やシグネチャが変わった項目で、"
    "どちらも Mermaid の可視性の記号ではない。可視性は図に出さない。"
    "変更前との比べがある図では、印の無い項目は変わっていないので名前だけを出し、型注記とシグネチャは省く。"
    "注記が「移した型」の型は、別のファイルから外形を変えずに動かした型で、項目の印は移す前の型との比べ。"
    "`<|--` は継承か実装、`-->` は項目の型、`..>` はメソッドのシグネチャに現れる型。"
    "`<<abstract>>` と項目の末尾の `*` は、言語の宣言から確かに分かるものだけに付く。"
    "印が無いことは具象であることを意味しない。"
)


def diagram_id(qualified_name: str) -> str:
    return re.sub(r"\W", "_", qualified_name)


def distinct_members(info: TypeInfo) -> list[list[Member]]:
    """図で一行になる項目ごとの組。一行が同じ項目は、宣言が何箇所にあっても一組にまとめる。

    別のファイルの同じ修飾名の型（Swift の private な型など）は索引で一つに併合され、
    同じ項目が重なる。変更した型の判定と印の付与には、まとめない項目の全部が要る。
    """
    groups: dict[str, list[Member]] = {}
    for member in members_of(info):
        groups.setdefault(render_member(member), []).append(member)
    return list(groups.values())


def class_block(
    info: TypeInfo,
    name: str,
    marks: dict[tuple[str, int], str],
    values: list[tuple[str, str]],
    note: str,
    *,
    compared: bool = False,
) -> list[str]:
    """型の塊。compared が真なら、印の無い項目は変わっていないので名前だけにする。"""
    lines = [f"    %% {name} {info.path}:{info.line} {note}"]
    lines.append(f'    class {diagram_id(name)}["{_flat(name)}"] {{')
    if info.kind != "class":
        lines.append(f"        <<{info.kind}>>")
    elif info.abstract:
        lines.append("        <<abstract>>")
    rows: list[tuple[str, str]] = []
    for group in distinct_members(info):
        # 重なった宣言の一つでも追加か変更なら、その印を残す
        mark = next((mark for m in group if (mark := marks.get((m.path, m.line), ""))), "")
        text = _flat(group[0].name) if compared and mark == "" else render_member(group[0])
        if (mark, text) not in rows:  # 名前だけにすると、引数違いの同名の項目が同じ行になる
            rows.append((mark, text))
    for mark, text in [*rows, *values]:
        lines.append(f"        {mark}{text}")
    lines.append("    }")
    return lines


def by_simple_name(shown: list[TypeInfo]) -> dict[str, list[TypeInfo]]:
    """単純名から型への引き先。"""
    by_simple: dict[str, list[TypeInfo]] = {}
    for info in shown:
        if info.kind != "module":  # 注記と基底の名前の引き先にならない（named と同じ規則）
            by_simple.setdefault(info.name, []).append(info)
    return by_simple


def _annotation_targets(member: Member, by_simple: dict[str, list[TypeInfo]]) -> list[TypeInfo]:
    """メンバの型と引数の型に現れる名前の、引き先の型。名前ごとに先頭の一つ。"""
    annotations = [member.type_name or "", *(p.type_name for p in member.parameters)]
    return [
        target
        for annotation in annotations
        for name in _IDENTIFIER.findall(annotation)
        for target in by_simple.get(name, [])[:1]
    ]


def relations(shown: list[TypeInfo], indexes: Indexes) -> list[str]:
    ids = {t.key: diagram_id(indexes.display(t)) for t in shown}
    by_simple = by_simple_name(shown)
    lines: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        if text not in seen:
            seen.add(text)
            lines.append(f"    {text}")

    for info in shown:
        for base in info.bases:
            for target in by_simple.get(base, [])[:1]:
                add(f"{ids[target.key]} <|-- {ids[info.key]}")
        for member in members_of(info):
            arrow = "..>" if member.kind in ("method", "constructor") else "-->"
            for target in _annotation_targets(member, by_simple):
                if target.key != info.key:
                    add(f"{ids[info.key]} {arrow} {ids[target.key]}")
    return sorted(lines)


def class_panel(blocks: list[str], shown: list[TypeInfo], indexes: Indexes) -> Panel:
    """型の塊の並びと、それらの間の関係を、一枚の classDiagram にする。"""
    return Panel("", "\n".join(["classDiagram", *blocks, *relations(shown, indexes)]))


def _note_of(*, added: bool, changed: bool, moved_from: TypeInfo | None) -> str:
    if moved_from is not None:
        return f"移した型（{moved_from.path}:{moved_from.line} から）"
    return "追加した型" if added else ("変更した型" if changed else "参照される型")


def _shape_change_note(change: TypeShapeChange) -> str:
    parts = ["基底を変えた"] if change.bases_changed else []
    if change.kind_changed:
        parts.append("種類を変えた")
    return "（" + "、".join(parts) + "）"


def _bases_text(bases: tuple[str, ...]) -> str:
    return "、".join(f"`{b}`" for b in bases) or "なし"


def _shape_change_entry(change: TypeShapeChange) -> str:
    parts = []
    if change.bases_changed:
        parts.append(
            f"基底: {_bases_text(change.bases_before)} → {_bases_text(change.bases_after)}"
        )
    if change.kind_changed:
        parts.append(f"種類: {change.kind_before} → {change.kind_after}")
    return f"`{change.name}`（{'、'.join(parts)}）"


def _trailing_notes(
    removed: list[str],
    changed_from: list[str],
    unsupported: int,
    shape_changes: list[TypeShapeChange],
) -> list[str]:
    """図の後ろに添える注記。削除した項目、`~` の項目の前の形、継承か種類の差、対象外の件数。"""
    trailing: list[str] = []
    if removed:
        trailing.append("変更で削除した項目: " + "、".join(f"`{r}`" for r in removed))
    if changed_from:
        trailing.append(
            "変更で形が変わった項目（`~`）の前の形: " + "、".join(f"`{c}`" for c in changed_from)
        )
    if shape_changes:
        trailing.append(
            "変更で継承か種類が変わった型: "
            + "、".join(_shape_change_entry(c) for c in shape_changes)
        )
    if unsupported:
        trailing.append(f"対象外にしたもの: 型を読めない言語のファイル {unsupported} 件。")
    return trailing


def class_diagram(review: Review) -> MermaidFigure:
    """クラス図。説明、Mermaid の図、削除した項目と省いた件数の注記を持つ。

    差分のレビューは変更後の木で作り、変更前の木の同じ型と比べて印を付ける。
    ファイルのレビューは変更後だけ。言語アダプタが無い（索引にできない）ファイルは対象外で、件数にする。
    """
    result = type_deltas(review)
    if not result.deltas:
        notes = f"{NO_CLASSES_TEXT}"
        if result.unsupported:
            notes += f" 型を読めない言語のファイル {len(result.unsupported)} 件は対象外。"
        # 図が無いので panels は空。文だけを説明に置く
        return MermaidFigure(notes, ())
    indexes = result.indexes
    blocks: list[str] = []
    for delta in result.deltas:
        shape_note = _shape_change_note(delta.shape) if delta.shape is not None else ""
        blocks += class_block(
            delta.info,
            indexes.display(delta.info),
            delta.member_marks,
            list(delta.value_marks),
            _note_of(added=delta.added, changed=delta.changed, moved_from=delta.moved_from)
            + shape_note,
            compared=result.before_indexes is not None,
        )
    trailing = _trailing_notes(
        [r for d in result.deltas for r in d.removed_members],
        [c for d in result.deltas for c in d.previous_forms],
        len(result.unsupported),
        [d.shape for d in result.deltas if d.shape is not None],
    )
    shown = [d.info for d in result.deltas]
    return MermaidFigure(
        LEGEND, (class_panel(blocks, shown, indexes),), "\n\n".join(trailing) or None
    )
