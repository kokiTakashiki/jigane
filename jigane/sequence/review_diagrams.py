"""レビューのシーケンス図の組み立て。`jigane sequence` と `jigane diagrams` が共有する。"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from jigane.figures.models import NoteText, Panel, SequenceFigure, SequenceFlow
from jigane.gates.tree_side import TreeSide
from jigane.git.tree_files import walk_tree_files
from jigane.languages import registry
from jigane.languages.base import SequenceAdapter
from jigane.reviews.lifecycle import Review
from jigane.sequence.before_diagrams import (
    NO_BASE_TREE_DISPLAY,
    NO_BASE_TREE_TEXT,
    BeforeAbsence,
    attach_before,
    before_diagrams,
)
from jigane.sequence.before_tree import load_before_tree
from jigane.sequence.diagram import (
    DEFAULT_DEPTH,
    MAX_DEPTH,
    MIN_DEPTH,
    DiagramInputs,
    EmptyEntryKind,
    EntryEvidence,
    construct_diagrams,
)
from jigane.sequence.diagram_json import diagrams_to_dict
from jigane.sequence.diagram_mermaid import to_mermaid
from jigane.sequence.diagram_select import region_ids, select_diagrams
from jigane.sequence.identifiers import identities_of
from jigane.sequence.index import build_index
from jigane.sequence.roots import (
    Root,
    SequenceError,
    changed_members,
    select_roots,
)
from jigane.sequence.side import Side

# 注記の文は二本で持つ。*_TEXT と *_NOTE は LLM に渡す資料の常体、*_DISPLAY は利用者に見せる敬体。
# 資料の文は評価の結果を動かさないため変えない
NO_ADAPTER_TEXT = "変更したファイルの言語に、呼び出しの抽出がまだ無い。"
NO_ADAPTER_DISPLAY = "変更したファイルの言語には、呼び出しの抽出がまだありません。"
NO_DECLARATION_TEXT = "変更した宣言も、変更したファイルを読む宣言も無い。"
NO_DECLARATION_DISPLAY = "変更した宣言も、変更したファイルを読む宣言もありません。"
NO_INTERACTION_TEXT = "変更した宣言はあるが、他の型とのやり取りが無い。"
NO_INTERACTION_DISPLAY = "変更した宣言はありますが、他の型とのやり取りがありません。"
SKIPPED_NAMES_SHOWN = 3
# 図にしない入口の区分ごとの注記。一覧の元の条件は sequences_of にある
NO_CALL_ENTRIES_NOTE = "呼び出しの無い入口 {count} 件は図にしない: {names}"
MARKUP_CALLED_ENTRIES_NOTE = "XAML から呼ばれる入口 {count} 件は図にしない: {names}"
UNRELATED_CALL_ENTRIES_NOTE = "変更に関わる呼び出しの無い入口 {count} 件は図にしない: {names}"
UNRESOLVED_CALL_ENTRIES_NOTE = "呼び先を解けない呼び出しがある入口 {count} 件は図にしない: {names}"
DROPPED_CHANGES_NOTE = "入口として拾えなかった変更 {count} 件: {names}"
NO_CALL_ENTRIES_DISPLAY = "呼び出しのない入口 {count} 件は図にしません: {names}"
MARKUP_CALLED_ENTRIES_DISPLAY = "XAML から呼ばれる入口 {count} 件は図にしません: {names}"
UNRELATED_CALL_ENTRIES_DISPLAY = "変更に関わる呼び出しのない入口 {count} 件は図にしません: {names}"
UNRESOLVED_CALL_ENTRIES_DISPLAY = (
    "呼び先を解けない呼び出しがある入口 {count} 件は図にしません: {names}"
)
DROPPED_CHANGES_DISPLAY = "入口として拾えなかった変更 {count} 件: {names}"


def _input_error(message: str) -> SequenceError:
    """利用者の指定の誤り。資料に入らないので、敬体と常体の区別を持たず同じ文を二本に渡す。"""
    return SequenceError(NoteText(message, message))


def _sequence_adapter(review: Review) -> SequenceAdapter | None:
    for path in review.paths:
        adapter = registry.adapter_for(path)
        if adapter is not None and isinstance(adapter, SequenceAdapter):
            return adapter
    return None


def _required_adapter(review: Review) -> SequenceAdapter:
    adapter = _sequence_adapter(review)
    if adapter is None:
        # 言語アダプタを持つファイルがあれば口が無いだけで、無ければ宣言を持たないファイルしか無い
        has_code = any(registry.adapter_for(path) is not None for path in review.paths)
        if has_code:
            raise SequenceError(NoteText(NO_ADAPTER_TEXT, NO_ADAPTER_DISPLAY))
        raise SequenceError(NoteText(NO_DECLARATION_TEXT, NO_DECLARATION_DISPLAY))
    return adapter


def build_diagrams(inputs: DiagramInputs, side: TreeSide = TreeSide.AFTER) -> dict[str, Any]:
    """図を作り、識別子を導いて辞書にする。side が BEFORE の図は、関係の矢印を足さない。"""
    built = construct_diagrams(inputs, relations=side is TreeSide.AFTER)
    return diagrams_to_dict(identities_of(built, side), built.notes, side)


def _assemble(
    review: Review,
    root: str | None,
    region: str | None,
    depth: int,
    *,
    full: bool,
    include_tests: bool,
    side: Side = Side.AFTER,
) -> tuple[dict[str, Any], int]:
    """図のモデルと、変更した宣言の数。"""
    if side is Side.BEFORE and review.base_tree_path is None:
        raise SequenceError(NoteText(NO_BASE_TREE_TEXT, NO_BASE_TREE_DISPLAY))
    if not MIN_DEPTH <= depth <= MAX_DEPTH:
        raise _input_error(
            f"--depth は {MIN_DEPTH} から {MAX_DEPTH} の範囲で指定してください: {depth}"
        )
    adapter = _required_adapter(review)
    tree_root = Path(review.repository_root)
    # 差分のレビューは変更後のツリー全体を、ファイルのレビューは対象のファイルを索引にする。
    # 索引は口を持つ言語のファイルだけを読む
    paths = (
        walk_tree_files(tree_root, tree_root)
        if review.base_tree_path is not None
        else review.paths
    )
    index = build_index(tree_root, paths, adapter)
    changed = changed_members(
        index, review.map_kind.segments, adapter, include_tests=include_tests
    )
    selection = select_roots(index, changed, adapter)
    regions = review.map_kind.table.regions
    if root is not None and not any(r.member.qualified_name == root for r in selection.roots):
        members = [m for m in changed.values() if m.qualified_name == root]
        if not members:
            raise _input_error(f"修飾名が変更した宣言にありません: {root}")
        named = replace(selection, roots=[Root(m) for m in members])
        inputs = DiagramInputs(index, named, regions, depth, full=full)
    else:
        inputs = DiagramInputs(index, selection, regions, depth, full=full)
    model = build_diagrams(inputs)
    if region is not None and region not in {r.id for r in regions}:
        raise _input_error(f"判断の箇所がありません: {region}")
    if side is not Side.AFTER:
        tree = load_before_tree(review, adapter, include_tests=include_tests)
        befores = before_diagrams(model, tree, inputs)
        attach_before(model, befores)
        model["side"] = side
    if side is Side.BEFORE:
        # 変更前の図を持つ入口だけが残り、図そのものは変更前の側になる
        model["diagrams"] = [
            {**d["before"], "before": None, "before_note": None}
            for d in model["diagrams"]
            if d["before"] is not None
        ]
    return select_diagrams(model, root, region), len(changed)


def build_model(
    review: Review,
    root: str | None,
    region: str | None,
    depth: int,
    *,
    full: bool,
    include_tests: bool,
    side: Side = Side.AFTER,
) -> dict[str, Any]:
    return _assemble(
        review, root, region, depth, full=full, include_tests=include_tests, side=side
    )[0]


def review_sequences(review: Review) -> SequenceFigure:
    """入口ごとの流れと、図を出せなかった理由などの一文の一覧を持つ図。"""
    # 変更前のツリーがある差分のレビューだけが、変更前と変更後の二つのパネルを持てる
    side = Side.BOTH if review.base_tree_path is not None else Side.AFTER
    try:
        model, changed_count = _assemble(
            review, None, None, DEFAULT_DEPTH, full=False, include_tests=False, side=side
        )
    except SequenceError as error:
        return SequenceFigure((), (error.note,))
    return sequences_of(model, changed_count)


def _panels_of(
    diagram: dict[str, Any], *, both: bool
) -> tuple[tuple[Panel, ...], BeforeAbsence | None]:
    """入口の図のパネルと注記。変更前の図が無い入口は変更後の一つと、その理由の注記になる。"""
    if not both:
        return (Panel("シーケンス図", to_mermaid(diagram)),), None
    after = Panel("変更後", to_mermaid(diagram))
    if diagram.get("before") is None:
        return (after,), diagram.get("before_note")
    return (Panel("変更前", to_mermaid(diagram["before"])), after), None


def _names_shown(names: list[str]) -> str:
    shown = "、".join(names[:SKIPPED_NAMES_SHOWN])
    if len(names) > SKIPPED_NAMES_SHOWN:
        shown += f"、ほか {len(names) - SKIPPED_NAMES_SHOWN} 件"
    return shown


@dataclass(frozen=True)
class FlowSource:
    """drawn_flows の入力。`build_diagrams` が返すモデルのうち、流れの描画が読む部分。

    diagrams は入口ごとの図の辞書で、キーの約束は `diagram_to_dict` が持つ。
    side が "both" のときだけ変更前の図を持てる。dropped は入口として拾えなかった変更の修飾名。
    """

    diagrams: tuple[dict[str, Any], ...]
    side: Side
    dropped: tuple[str, ...]

    @classmethod
    def from_model(cls, model: Mapping[str, Any]) -> "FlowSource":
        return cls(
            tuple(model["diagrams"]),
            Side(model.get("side", Side.AFTER)),
            tuple(model.get("dropped", ())),
        )


def drawn_flows(source: FlowSource) -> SequenceFigure:
    """呼び出しのある入口だけを呼び出しの多い順に並べた流れと、図にしなかった入口の注記。"""
    # sorted は安定なので、呼び出しの数が同じ入口は元の順のまま残る
    drawn = sorted((d for d in source.diagrams if d["arrows"]), key=lambda d: -len(d["arrows"]))
    # 呼び出しを辿れて一つも無い入口だけを「無い」と断定する。受け手の型を解けなかった呼び出しが
    # 一つでもあれば、辿れなかっただけかもしれないので別に並べる
    empty = [d for d in source.diagrams if not d["arrows"]]
    roots_by_kind: dict[EmptyEntryKind, list[str]] = {kind: [] for kind in EmptyEntryKind}
    for d in empty:
        roots_by_kind[EntryEvidence.from_dict(d).kind()].append(d["root"])
    # 呼び出しはあるが、どれも変更した宣言へ届かず矢印にしなかった入口は、呼び出しが無いのではない
    both = source.side is Side.BOTH
    sequences = []
    for d in drawn:
        panels, note = _panels_of(d, both=both)
        # 変更前の図にだけ載る判断の箇所も、その入口の判断の箇所に数える
        shown = [*region_ids(d), *(region_ids(d["before"]) if both and d.get("before") else [])]
        sequences.append(
            SequenceFlow(
                f"{d['root']}（{d['path']}:{d['line']}）",
                panels,
                None if note is None else note.note,
                tuple(dict.fromkeys(shown)),
            )
        )
    notes: list[NoteText] = []
    for template, display_template, roots in (
        (NO_CALL_ENTRIES_NOTE, NO_CALL_ENTRIES_DISPLAY, roots_by_kind[EmptyEntryKind.NO_CALL]),
        (
            MARKUP_CALLED_ENTRIES_NOTE,
            MARKUP_CALLED_ENTRIES_DISPLAY,
            roots_by_kind[EmptyEntryKind.MARKUP_CALLED],
        ),
        (
            UNRELATED_CALL_ENTRIES_NOTE,
            UNRELATED_CALL_ENTRIES_DISPLAY,
            roots_by_kind[EmptyEntryKind.UNRELATED_CALL],
        ),
        (
            UNRESOLVED_CALL_ENTRIES_NOTE,
            UNRESOLVED_CALL_ENTRIES_DISPLAY,
            roots_by_kind[EmptyEntryKind.UNRESOLVED_CALL],
        ),
        (DROPPED_CHANGES_NOTE, DROPPED_CHANGES_DISPLAY, list(source.dropped)),
    ):
        if roots:
            fields = {"count": len(roots), "names": _names_shown(roots)}
            notes.append(NoteText(template.format(**fields), display_template.format(**fields)))
    return SequenceFigure(tuple(sequences), tuple(notes))


def sequences_of(model: dict[str, Any], changed_count: int) -> SequenceFigure:
    """drawn_flows に、図が一枚も無いときの理由を足す。

    changed_count は変更した宣言の数。理由を選ぶ。
    """
    figure = drawn_flows(FlowSource.from_model(model))
    if figure.flows:
        return figure
    if changed_count == 0:
        reason = NoteText(NO_DECLARATION_TEXT, NO_DECLARATION_DISPLAY)
    else:
        reason = NoteText(NO_INTERACTION_TEXT, NO_INTERACTION_DISPLAY)
    return SequenceFigure(figure.flows, (*figure.notes, reason))


def sequence_figure(review: Review) -> SequenceFigure:
    """登録簿のシーケンス図の項が作る図。入口ごとの流れと、図を出せなかった理由の文を持つ。"""
    return review_sequences(review)
