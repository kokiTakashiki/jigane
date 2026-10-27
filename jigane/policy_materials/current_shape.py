"""保存済みのレビューを経ずに、ファイルと型の名前の集合から、いまのコードの形を図にする。

変更のレビューで使う図と同じ描き方を使う。変更の印は付けず、コードの全文も載せない。
"""

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from jigane.figures.models import (
    MermaidFigure,
    NoteText,
    SequenceFigure,
    SequenceFlow,
    TreeFigure,
)
from jigane.policy_materials import changed_files
from jigane.policy_materials.classes import (
    class_block,
    class_panel,
    members_of,
    referenced_types,
)
from jigane.sequence.diagram import DEFAULT_DEPTH, build_diagrams
from jigane.sequence.index import TypeInfo
from jigane.sequence.indexes import Indexes
from jigane.sequence.review_diagrams import FlowSource, drawn_flows
from jigane.sequence.roots import Root, RootSelection

NO_NAMED_CLASSES_TEXT = "名を挙げた型が、選んだファイルの索引に無い。"
NO_NAMED_ENTRIES_TEXT = "名を挙げたメソッドと関数が、選んだファイルの索引に無い。"
CURRENT_CLASSES_LEGEND = (
    "名を挙げた型と、その項目の型注記とシグネチャに現れる型（一段だけ）を、型と項目の宣言から"
    "作った。関数の本体は含まない。型の直前の `%%` の行が、型の宣言のパスと行。"
    "変更の印は付けない。可視性は図に出さない。"
    "`<|--` は継承か実装、`-->` は項目の型、`..>` はメソッドのシグネチャに現れる型。"
)


@dataclass(frozen=True)
class CurrentShape:
    """いまの形の三つの図。"""

    tree: TreeFigure
    classes: MermaidFigure
    sequences: SequenceFigure


def current_shape(root: Path, paths: Collection[str], names: Collection[str]) -> CurrentShape:
    """root の下の paths のファイルについて、names に挙げた型と入口から三つの図を描く。

    paths は root からの相対パス（区切りは `/`）。names は型と、メソッドや関数の修飾名で、
    索引にある名前に引き当たらないものは図に出ない。言語アダプタの無いファイルはツリー図にだけ出る。
    """
    ordered = sorted(set(paths))
    wanted = set(names)
    indexes = Indexes(root, ordered)
    return CurrentShape(
        changed_files.current_tree_figure(ordered),
        _classes(indexes, wanted, set(ordered)),
        _sequences(indexes, wanted, set(ordered)),
    )


def _named_types(indexes: Indexes, wanted: set[str], paths: set[str]) -> list[TypeInfo]:
    found = [
        info
        for info in indexes.types()
        if info.qualified_name in wanted
        and info.path in paths
        # ファイル全体を表すだけの型で、図に出す外形が無い
        and not (info.kind == "module" and not members_of(info))
    ]
    return sorted(found, key=lambda t: (t.path, t.line))


def _classes(indexes: Indexes, wanted: set[str], paths: set[str]) -> MermaidFigure:
    named = _named_types(indexes, wanted, paths)
    if not named:
        # 図が無いので panels は空。文だけを説明に置く
        return MermaidFigure(NO_NAMED_CLASSES_TEXT, ())
    shown = [*named, *referenced_types(indexes, named)]
    named_keys = {t.key for t in named}
    blocks: list[str] = []
    for info in shown:
        note = "名を挙げた型" if info.key in named_keys else "参照される型"
        values = [(("", value)) for value in indexes.enum_values(info)]
        blocks += class_block(info, indexes.display(info), {}, values, note)
    return MermaidFigure(CURRENT_CLASSES_LEGEND, (class_panel(blocks, shown, indexes),))


def _sequences(indexes: Indexes, wanted: set[str], paths: set[str]) -> SequenceFigure:
    flows: list[SequenceFlow] = []
    notes: list[NoteText] = []
    for index in indexes.indexes:
        members = [
            m
            for m in index.callables.values()
            if m.qualified_name in wanted
            and m.path in paths
            and m.declaration is not None
            and m.declaration.body is not None
        ]
        if not members:
            continue
        # 変更の概念が無いので、辿る先を絞らず（full）、入口は名を挙げたものに限る
        model = build_diagrams(
            index,
            RootSelection({}, []),
            [],
            DEFAULT_DEPTH,
            full=True,
            roots=[Root(m) for m in members],
        )
        drawn = drawn_flows(FlowSource.from_model(model))
        flows += drawn.flows
        notes += drawn.notes
    if not flows and not notes:
        # この図は資料の文（text）にしか届かず、表示の文は使われない。型が片方だけを許さないため、
        # 資料の文と同じ文を表示の文にも渡す
        notes.append(NoteText(NO_NAMED_ENTRIES_TEXT, NO_NAMED_ENTRIES_TEXT))
    return SequenceFigure(tuple(flows), tuple(notes))
