"""レビューの概要の行。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, assert_never

from jigane.design.review_documents import attachment_candidates
from jigane.findings.records import HistoryLoad
from jigane.gates.entry import Allowed, FlakyDetail, RegexDetail
from jigane.judgment_map.classification import Segment
from jigane.judgment_map.kinds import Kind
from jigane.judgment_map.map_kind import CodeMap, DesignDocMap
from jigane.judgment_map.region_table import CodeRegion, SectionRegion
from jigane.languages.plugins import one_line_text, sanitize_text
from jigane.pull_request_context.background import OWNERLESS_TEXT, PastPullRequest, has_owners
from jigane.pull_request_context.bots import read_bot_comments
from jigane.reviews import selection as selection_module
from jigane.reviews.lifecycle import (
    DesignDocOrigin,
    FeatureOrigin,
    PullRequestOrigin,
    RangeOrigin,
    Review,
)
from jigane.static_analysis.intake import StaticFindingsStore, read_static_findings

SHORT_SHA = 7

EXCERPT_PREVIEW_LINES = 5


def total_lines(review: Review) -> tuple[int, int]:
    by_level = review.map_kind.table.line_counts.by_level
    return by_level["required"], sum(by_level.values())


def map_lines(review: Review) -> list[str]:
    kind = review.map_kind
    counts = kind.table.line_counts
    required, total = total_lines(review)
    lines = [f"判断の地図: 人の判断が要る行 {required} / 全体 {total}"]
    levels = counts.by_level
    lines.append(
        f"  判断の要否: 重要 {levels['required']}、確認 {levels['confirm']}、"
        f"機械 {levels['machine']}"
    )
    labels = counts.section_kinds.labels if counts.section_kinds else {}
    kinds = ", ".join(f"{labels.get(k, k)} {n}" for k, n in counts.by_kind.items() if n)
    lines.append(f"  種類: {kinds or 'なし'}")
    regions = kind.table.regions
    lines.append(f"判断の箇所: {len(regions)} 件")
    for region in regions:
        span = region.new_span or region.old_span
        location = one_line_text(region.path)
        if span:
            location += f":{span.start_line}-{span.end_line}"
        match region:
            case CodeRegion():
                label = region.kind.value
            case SectionRegion():
                label = region.kind_label
            case _ as unreachable:
                assert_never(unreachable)
        lines.append(f"  {region.id} {label} {location}")
        lines.append(f"      {one_line_text(region.summary)}")
    match kind:
        case DesignDocMap():
            # 設計書の地図の箇所は節で、推しのコードの箇所は「いまの形」の参考として件数だけ出す
            form = kind.current_form
            form_levels = form.line_counts.by_level
            lines.append(
                f"いまの形（推しのコード）: 判断の箇所 {len(form.regions)} 件、"
                f"重要 {form_levels['required']} / 全体 {sum(form_levels.values())}"
            )
        case CodeMap():
            # コードの地図に「いまの形」は無い
            pass
        case _:
            assert_never(kind)
    return lines


def _attachment_lines(review: Review, design_doc: DesignDocOrigin) -> list[str]:
    """付属の文書と、まだ付けていない候補（設計書と同じ場所の reports/ の .md）。"""
    attached = {item.path for item in design_doc.attachments}
    candidates = [
        path
        for path in attachment_candidates(Path(review.repository_root), design_doc.document_path)
        if path not in attached
    ]
    lines: list[str] = []
    if design_doc.attachments:
        lines.append(f"  付属の文書: {len(design_doc.attachments)} 件")
        lines += [
            f"    {one_line_text(item.path)}（版 {item.version[:SHORT_SHA]}）"
            for item in design_doc.attachments
        ]
    if candidates:
        lines.append("  付属の候補（--with で選ぶ）:")
        lines += [f"    {one_line_text(path)}" for path in candidates]
    return lines


def origin_lines(review: Review) -> list[str]:
    """種類ごとの出所の行。PR は題と URL、範囲は範囲の文字列、設計書は版と選択、機能は持たない。

    new、show、refresh、list はここから引く。
    """
    match review.origin:
        case PullRequestOrigin(info=info):
            return [f"PR: {info.title}", f"  {info.url}"]
        case RangeOrigin(range=local_range):
            return [f"範囲: {local_range.range_text}"]
        case FeatureOrigin():
            return []
        case DesignDocOrigin() as design_doc:
            return [
                (
                    f"設計書: {one_line_text(design_doc.document_path)}"
                    f"（版 {design_doc.document_version[:SHORT_SHA]}）"
                ),
                f"  いまのコードの版 {design_doc.code_version[:SHORT_SHA]}",
                f"  選んだファイル: {len(design_doc.selection)} 件",
                *(f"    {one_line_text(path)}" for path in design_doc.selection),
                *_attachment_lines(review, design_doc),
            ]
        case _ as unreachable:
            assert_never(unreachable)


def source_lines(review: Review) -> list[str]:
    """出所の行に、PR と範囲では合流点とコミットが続く。"""
    head = origin_lines(review)
    source = review.diff_origin
    if not head or source is None:
        return head
    return [
        *head,
        f"  merge-base {source.merge_base[:SHORT_SHA]}、head {source.head_sha[:SHORT_SHA]}",
    ]


def background_lines(review: Review) -> list[str]:
    background = review.background
    if background is None:
        return []
    lines = ["背景:"]
    lines.extend(f"  参照する文書 {one_line_text(path)}" for path in background.documents)
    for past in background.past_changes:
        label = (
            f"過去の PR #{past.number}"
            if isinstance(past, PastPullRequest)
            else f"過去のコミット {past.sha[:SHORT_SHA]}"
        )
        lines.append(
            f"  {label} {one_line_text(past.title)}（{one_line_text(', '.join(past.files))}）"
        )
    for item in background.owners if has_owners(background) else []:
        owners = one_line_text(", ".join(item.owners)) or OWNERLESS_TEXT
        lines.append(f"  所有者 {one_line_text(item.path)}: {owners}")
    if len(lines) == 1:
        lines.append("  背景の材料はありません")
    return lines


def bot_lines(review: Review) -> list[str]:
    saved = read_bot_comments(review.id.directory())
    if saved is None:
        return []
    responses = [
        region
        for region in review.map_kind.table.regions
        if isinstance(region, CodeRegion) and region.kind is Kind.BOT_RESPONSE
    ]
    lines = [
        (
            f"ボットのコメント: {len(saved.judgments)} 件"
            f"（判定から外した {saved.unavailable_count} 件）"
        ),
        f"  指摘への対応の判断の箇所: {len(responses)} 件",
    ]
    lines.extend(
        f"    {region.id} {one_line_text(region.path)} {one_line_text(region.summary)}"
        for region in responses
    )
    return lines


@dataclass(frozen=True)
class _StaticView:
    store: StaticFindingsStore
    raised_regions: int
    # 区間と、その new_span に重なった指摘の識別子。指摘の無い区間は含めない
    by_segment: tuple[tuple[Segment, tuple[str, ...]], ...]
    outside_diff: tuple[str, ...]


def _static_view(review: Review) -> _StaticView | None:
    store = read_static_findings(review.id.directory())
    if store is None:
        return None
    raised = sum(
        isinstance(region, CodeRegion) and region.kind is Kind.STATIC_FINDING
        for region in review.map_kind.table.regions
    )
    by_segment = tuple(
        (segment, segment.static_finding_ids)
        for segment in review.map_kind.segments
        if segment.static_finding_ids
    )
    in_diff = {finding_id for _, ids in by_segment for finding_id in ids}
    outside = tuple(item.id for item in store.findings if item.id not in in_diff)
    return _StaticView(store, raised, by_segment, outside)


def static_finding_lines(review: Review) -> list[str]:
    view = _static_view(review)
    if view is None:
        return []
    store = view.store
    lines = [
        f"静的検査の指摘: {len(store.findings)} 件",
        f"  上げた判断の箇所: {view.raised_regions} 件、差分の外: {len(view.outside_diff)} 件",
    ]
    reasons = ", ".join(f"{reason} {count}" for reason, count in store.dropped.items())
    dropped = f"  捨てた指摘: {sum(store.dropped.values())} 件"
    lines.append(f"{dropped}（{reasons}）" if reasons else dropped)
    by_id = {item.id: item for item in store.findings}
    for segment, ids in view.by_segment:
        span = segment.new_span
        path = one_line_text(str(segment.new_path))
        location = f"{path}:{span.start_line}-{span.end_line}" if span else ""
        lines.append(f"  区間 {location}")
        for finding_id in ids:
            finding = by_id[finding_id]
            lines.append(
                f"    {finding.id} {one_line_text(finding.tool_name)} "
                f"{one_line_text(finding.rule_id)} {finding.level}: "
                # 複数行の文は字下げの階層を崩すので、概要では一行に畳む
                f"{one_line_text(finding.message)}"
            )
    return lines


def static_findings_json(review: Review) -> dict[str, Any] | None:
    """show --json の static_findings の欄。保存した指摘が無ければ None。"""
    view = _static_view(review)
    if view is None:
        return None
    store = view.store
    return {
        "summary": {
            "finding_count": len(store.findings),
            "raised_region_count": view.raised_regions,
            "outside_diff_count": len(view.outside_diff),
            "dropped": dict(store.dropped),
        },
        "findings": [
            {**item.model_dump(mode="json"), "message": sanitize_text(item.message)}
            for item in store.findings
        ],
        "segments": [
            {
                "path": segment.new_path,
                "start_line": segment.new_span.start_line if segment.new_span else None,
                "end_line": segment.new_span.end_line if segment.new_span else None,
                "finding_ids": list(ids),
            }
            for segment, ids in view.by_segment
        ],
    }


def warning_lines(review: Review) -> list[str]:
    return [f"警告: {warning}" for warning in review.warnings]


def declaration_lines(review: Review) -> list[str]:
    declaration = review.declaration
    if declaration.intent is not None:
        return [
            f"宣言（{declaration.source.label}）:",
            *(f"  {sanitize_text(line)}" for line in declaration.intent.splitlines()),
        ]
    lines = [f"宣言（{declaration.source.label} {len(declaration.excerpts)} 件）:"]
    for excerpt in declaration.excerpts:
        lines.append(f"  {one_line_text(excerpt.path)}:{excerpt.start_line}-{excerpt.end_line}")
        preview = excerpt.text.splitlines()
        lines.extend(f"    {sanitize_text(line)}" for line in preview[:EXCERPT_PREVIEW_LINES])
        if len(preview) > EXCERPT_PREVIEW_LINES:
            lines.append("    ...")
    return lines


def gate_lines(review: Review) -> list[str]:
    gates = review.gates
    if gates is None:
        return []
    header = f"ゲート違反 {gates.counted_count} 件、警告 {len(gates.warnings)} 件"
    # 注記で外した札は終了コードに数えないので、違反の件数とは別に見せる
    lines = [header + (f"、外した札 {gates.allowed_count} 件" if gates.allowed_count else "")]
    for entry in [*gates.violations, *gates.warnings]:
        head = f"  {entry.number} {entry.id} {one_line_text(entry.path)}:{entry.line}"
        match entry.detail:
            case RegexDetail() if isinstance(entry.state, Allowed):
                lines.append(
                    f"{head}（{entry.severity}）外しました（{entry.state.reason}）"
                    f" {entry.explanation['text']}"
                )
            case RegexDetail():
                lines.append(f"{head}（{entry.severity}） {entry.explanation['text']}")
            case FlakyDetail(cause=cause, reason=reason):
                lines.append(f"{head}（{cause}）" + (f" {reason}" if reason else ""))
    return lines


def has_gate_violation(review: Review) -> bool:
    """終了コードに数える違反の有無。"""
    return review.gates is not None and review.gates.blocks_exit


def selection_lines(review: Review, history: HistoryLoad) -> list[str]:
    directory = review.id.directory()
    summary = selection_module.summary(directory, history)
    lines = [
        (
            f"最終的な指摘 {len(summary['picked'])} 件、保留 {len(summary['held'])} 件、"
            f"異議 {len(summary['objections'])} 件"
        ),
    ]
    if summary["stale"]:
        lines.append(
            f"古い選択（今の指摘に無い番号）: {', '.join(summary['stale'])}"
            "。スキルの再実行で番号が新しくなりました"
        )
        # 履歴から本文を引けた番号は、前の回の指摘として本文を示す
        lines.extend(
            f"  {detail.text()}"
            for detail in selection_module.stale_details(
                directory, selection_module.load_selection(directory), history
            )
            if detail.in_history
        )
    return lines
