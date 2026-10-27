"""方針のレビューに渡すクラス図。変更した型の外形を Mermaid の classDiagram にする。

型とメンバの宣言だけから作り、関数の本体は読まない。図の読み方は LEGEND にある。
"""

import re
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from jigane.figures.models import MermaidFigure, Panel
from jigane.git.tree_files import walk_tree_files
from jigane.judgment_map.classification import Segment
from jigane.languages import registry
from jigane.languages.base import SequenceAdapter
from jigane.policy_materials import changed_files
from jigane.policy_materials.moved_declarations import PlacedDeclaration, find_moved
from jigane.reviews.lifecycle import Review
from jigane.sequence.index import Member, TypeInfo, TypeKey
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
)
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def diagram_id(qualified_name: str) -> str:
    return re.sub(r"\W", "_", qualified_name)


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
        return _flat(f"{member.name}({parameters}){returns}")
    return _flat(f"{member.name}: {member.type_name}" if member.type_name else member.name)


def members_of(info: TypeInfo) -> list[Member]:
    return sorted(
        (m for ms in info.members.values() for m in ms), key=lambda m: (m.path, m.line, m.name)
    )


def distinct_members(info: TypeInfo) -> list[list[Member]]:
    """図で一行になる項目ごとの組。一行が同じ項目は、宣言が何箇所にあっても一組にまとめる。

    別のファイルの同じ修飾名の型（Swift の private な型など）は索引で一つに併合され、
    同じ項目が重なる。変更した型の判定と印の付与には、まとめない項目の全部が要る。
    """
    groups: dict[str, list[Member]] = {}
    for member in members_of(info):
        groups.setdefault(render_member(member), []).append(member)
    return list(groups.values())


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


def _by_simple_name(shown: list[TypeInfo]) -> dict[str, list[TypeInfo]]:
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
    by_simple = _by_simple_name(shown)
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


def _trailing_notes(removed: list[str], changed_from: list[str], unsupported: int) -> list[str]:
    """図の後ろに添える注記。削除した項目、`~` の項目の前の形、対象外にした件数。"""
    trailing: list[str] = []
    if removed:
        trailing.append("変更で削除した項目: " + "、".join(f"`{r}`" for r in removed))
    if changed_from:
        trailing.append(
            "変更で形が変わった項目（`~`）の前の形: " + "、".join(f"`{c}`" for c in changed_from)
        )
    if unsupported:
        trailing.append(f"対象外にしたもの: 型を読めない言語のファイル {unsupported} 件。")
    return trailing


def class_diagram(review: Review) -> MermaidFigure:
    """クラス図。説明、Mermaid の図、削除した項目と省いた件数の注記を持つ。

    差分レビュー式は変更後の木で作り、変更前の木の同じ型と比べて印を付ける。
    機能レビュー式は変更後だけ。言語アダプタが無い（索引にできない）ファイルは対象外で、件数にする。
    """
    root = Path(review.repository_root)
    changed_paths = [p for p, e in changed_files.file_changes(review).items() if e.exists_after]
    unsupported = [
        p for p in changed_paths if not isinstance(registry.adapter_for(p), SequenceAdapter)
    ]
    paths = walk_tree_files(root, root) if review.is_diff else review.paths
    indexes = Indexes(root, paths)
    segments = review.map_kind.segments
    changed = _without_unchanged_variables(_changed_types(indexes, segments), segments)
    if not changed:
        notes = f"{NO_CLASSES_TEXT}"
        if unsupported:
            notes += f" 型を読めない言語のファイル {len(unsupported)} 件は対象外。"
        # 図が無いので panels は空。文だけを説明に置く
        return MermaidFigure(notes, ())
    referenced = _without_unchanged_variables(referenced_types(indexes, changed), segments)
    before_indexes: Indexes | None = None
    if review.base_tree_path is not None:
        before_paths = list(changed_files.file_changes(review, before=True))
        before_indexes = Indexes(review.base_tree_path, before_paths)
    ordered = [*changed, *referenced]
    shown = ordered
    changed_keys = {t.key for t in changed}
    moved = _moved_origins(indexes, before_indexes, changed) if before_indexes is not None else {}
    blocks: list[str] = []
    removed: list[str] = []
    changed_from: list[str] = []
    for info in shown:
        is_changed = info.key in changed_keys
        marks: dict[tuple[str, int], str] = {}
        added = False
        moved_from = None
        shown_name = indexes.display(info)
        after_values = indexes.enum_values(info)
        value_marks = dict.fromkeys(after_values, "")
        if is_changed and before_indexes is not None:
            moved_from = moved.get(info.key)
            before = moved_from or before_indexes.find(info.key)
            added = before is None
            # 外した変数を削除と取り違えないよう、印は外す前の型から作る
            marks, gone, previous = _marks(indexes.find(info.key) or info, shown_name, before)
            removed += gone
            changed_from += previous
            before_values = [] if before is None else before_indexes.enum_values(before)
            value_marks = {v: "" if v in before_values else "+" for v in after_values}
            removed += [f"{shown_name}.{v}" for v in before_values if v not in after_values]
            if added:
                marks = {(m.path, m.line): "+" for m in members_of(info)}
        blocks += class_block(
            info,
            shown_name,
            marks,
            list(value_marks.items()),
            _note_of(added=added, changed=is_changed, moved_from=moved_from),
            compared=before_indexes is not None,
        )
    trailing = _trailing_notes(removed, changed_from, len(unsupported))
    return MermaidFigure(
        LEGEND, (class_panel(blocks, shown, indexes),), "\n\n".join(trailing) or None
    )
