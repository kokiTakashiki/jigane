"""指摘の行。"""

from collections.abc import Sequence
from typing import Any, assert_never

from jigane.findings.discard import (
    DISCARDED_VIEWPOINT,
    NO_CONCERN_CLAIM,
    Considered,
    concern_with_places,
    places_line,
)
from jigane.findings.discarded_nits import selection_note_of
from jigane.findings.records import (
    Adjudication,
    Alternative,
    DisplayCategory,
    OutOfScopeDecision,
    Placement,
    Rebuttal,
    StoredTheme,
    TriagedFinding,
    count_heading,
    display_category_of,
    display_info,
    group_by_display_category,
    group_by_triage,
)
from jigane.judgment_map.region_table import CodeRegion, SectionRegion
from jigane.languages.plugins import one_line_text
from jigane.reviews import selection as selection_module
from jigane.reviews.lifecycle import Review
from jigane.skills.received_concerns import NoneReceived, NotRecorded, Received, ReceivedConcerns
from jigane.skills.run_record import StoppedView

WHOLE_CHANGE = "変更全体"


def location(location: dict[str, Any]) -> str:
    return f"{one_line_text(location['path'])}:{location['start_line']}-{location['end_line']}"


def adjudication_lines(adjudication: Adjudication) -> list[str]:
    match adjudication:
        case Rebuttal():
            parts = [("反証", adjudication.challenge)]
        case Alternative():
            parts = [("代案", adjudication.alternative), ("擁護", adjudication.defense)]
        case _:
            assert_never(adjudication)
    parts.append(("判定", f"{adjudication.verdict.value}（{adjudication.reason}）"))
    return [f"      {label}: {text}" for label, text in parts]


def selection_marks(review: Review) -> dict[str, str]:
    """F の番号から、picked、held、異議の印の文字列への対応。印の無い指摘は含まない。"""
    selection = selection_module.load_selection(review.id.directory())
    marks: dict[str, list[str]] = {}
    for number in selection.picked:
        marks.setdefault(number, []).append("picked")
    for number in selection.held:
        marks.setdefault(number, []).append("held")
    for number in selection.objections:
        marks.setdefault(number, []).append("異議")
    return {number: f" [{', '.join(names)}]" for number, names in marks.items()}


def finding_lines(finding: TriagedFinding, marks: dict[str, str]) -> list[str]:
    body = finding.finding
    mark = marks.get(body.id, "")
    evidence = body.extra["evidence"]
    # nit は開示の場所を添えた懸念の文で出す。懸念の文が無い nit は、捨てた理由の文で
    # 代えず、ほかの nit で懸念の文が空のときと同じに空で出す
    claim = body.claim
    places: list[str] = []
    if body.nit_reason is not None:
        claim = concern_with_places(body.nit_reason) or NO_CONCERN_CLAIM
        if claim == NO_CONCERN_CLAIM and (line := places_line(body.nit_reason)) is not None:
            places = [f"      {line}"]
    # 捨てた候補から写した nit の観点は印であり、利用者には見せない
    viewpoint = "" if body.viewpoint == DISCARDED_VIEWPOINT else body.viewpoint
    note = selection_note_of(body.id)
    note_mark = "" if note is None else f" [{note}]"
    return [
        f"  {body.id}{mark} {body.severity} {viewpoint} {claim}{note_mark}",
        *places,
        # 捨てた候補から写した指摘は根拠の位置を持たない
        *([f"      根拠: {', '.join(location(item) for item in evidence)}"] if evidence else []),
        *(adjudication_lines(body.adjudication) if body.adjudication is not None else []),
    ]


def considered_lines(considered: Considered | None) -> list[str]:
    """検討した候補の件数の行。捨てた件数と捨てた理由は利用者に見せない。

    considered が None のときは、検討の記録を保存する前の古い記録で、何も言えないので出さない。
    """
    if considered is None:
        return []
    if considered.discovered == 0:
        return ["検討した候補はありません"]
    return [f"検討 {considered.discovered} 件"]


def _scope_section_lines(
    label: str, members: list[TriagedFinding], marks: dict[str, str]
) -> list[str]:
    lines = [f"<{label}>"]
    for finding in members:
        # 範囲外の区分は judge_scope の記録と同時にしか付かない（schema も必須にする）
        if not isinstance(finding.decision, OutOfScopeDecision):
            raise TypeError(f"範囲外の節に範囲外でない決定がある: {finding.finding.id}")
        detail = finding.decision.out_of_scope
        lines.append(f"{label}: {', '.join(detail['paths']) or '範囲内の根拠のみ'}")
        lines.append(f"      理由: {detail['reason']}")
        lines.extend(finding_lines(finding, marks))
    return lines


def _in_order_section_lines(
    label: str,
    members: list[TriagedFinding],
    regions: dict[str, CodeRegion | SectionRegion],
    marks: dict[str, str],
) -> list[str]:
    # 畳むのは実装のテーマの形で、方針の群には畳む区分が付かない。並びは判断の箇所ごと
    lines = [f"<{label}>"]
    groups: dict[str, list[TriagedFinding]] = {}
    for finding in members:
        region = regions.get(finding.finding.extra["judgment_region"])
        title = f"{region.id} {region.summary}" if region else WHOLE_CHANGE
        groups.setdefault(title, []).append(finding)
    for title, group in groups.items():
        lines.append(f"[{title}]")
        for finding in group:
            lines.extend(finding_lines(finding, marks))
    return lines


def macro_view_lines(
    review: Review,
    findings: list[TriagedFinding],
    marks: dict[str, str],
    considered: Considered | None = None,
) -> list[str]:
    regions = {r.key: r for r in review.map_kind.table.regions}
    lines = [count_heading("方針の指摘", findings), *considered_lines(considered)]
    for triage, members in group_by_triage(findings):
        label = triage.traits.label
        match triage.traits.placement:
            case Placement.DISMISSED_SECTION:
                lines.append(f"[{label}]")
                for finding in members:
                    lines.extend(finding_lines(finding, marks))
            case Placement.SCOPE_SECTION:
                lines.extend(_scope_section_lines(label, members, marks))
            case Placement.IN_ORDER | Placement.COLLAPSED:
                lines.extend(_in_order_section_lines(label, members, regions, marks))
            case _:
                assert_never(triage.traits.placement)
    return lines


def micro_count_heading(findings: Sequence[TriagedFinding]) -> str:
    """実装の群の件数の見出し。全件を見せるので棄却も件数に数え、うち棄却を添える。"""
    rejected = sum(1 for f in findings if display_category_of(f) is DisplayCategory.REJECTED)
    return f"実装の指摘: {len(findings)} 件（うち棄却 {rejected} 件）"


def _themed_lines(
    members: list[TriagedFinding], themes: Sequence[StoredTheme], marks: dict[str, str]
) -> list[str]:
    """必読、未検証、残りの中身。テーマの束ね（[Bn 題]）を保ち、テーマ外の指摘は末尾に並べる。"""
    remaining = {f.finding.id: f for f in members}
    lines: list[str] = []
    for theme in themes:
        bundled = [remaining.pop(i) for i in theme.finding_ids if i in remaining]
        if not bundled:
            continue
        lines.append(f"  [{theme.id} {theme.title}] {theme.severity}")
        for finding in bundled:
            lines.extend(finding_lines(finding, marks))
    for finding in remaining.values():
        lines.extend(finding_lines(finding, marks))
    return lines


def micro_view_lines(
    findings: Sequence[TriagedFinding],
    themes: Sequence[StoredTheme],
    marks: dict[str, str],
) -> list[str]:
    """保存した全ての指摘を、表示の分類の見出しで分けて畳まずに出す。空の分類は見出しも出さない。"""
    lines = [micro_count_heading(findings)]
    for category, members in group_by_display_category(findings):
        lines.append(f"<{display_info(category).label}> {len(members)} 件")
        match category:
            case (
                DisplayCategory.MUST_READ | DisplayCategory.UNVERIFIED | DisplayCategory.REMAINING
            ):
                lines.extend(_themed_lines(members, themes, marks))
            case (
                DisplayCategory.NIT
                | DisplayCategory.DISCLOSED_LIMIT
                | DisplayCategory.OUT_OF_SCOPE
                | DisplayCategory.REJECTED
            ):
                # テーマの束ねは読む順を付けるためのもの。これらは重みで読まないので並べるだけ
                for finding in members:
                    lines.extend(finding_lines(finding, marks))
            case _:
                assert_never(category)
    return lines


def received_concern_lines(received: ReceivedConcerns) -> list[str]:
    """「方針から受け取った懸念」の節。記録が無い実行は一行で知らせ、懸念を渡していない実行は節を出さない。"""
    match received:
        case NotRecorded():
            return ["[方針から受け取った懸念]", "  この実行には受けた懸念の記録がありません"]
        case NoneReceived():
            # 渡した懸念が無い実行は、節ごと出さない
            return []
        case Received(concerns=concerns, unspecified=unspecified):
            lines = ["[方針から受け取った懸念]"]
            for concern in concerns:
                shards = ", ".join(str(number) for number in concern.shards)
                made = (
                    ", ".join(concern.finding_ids)
                    if concern.finding_ids
                    else "指摘にしませんでした"
                )
                lines.append(
                    f"  {concern.id} {concern.text} 受けたシャード: {shards} 作られた指摘: {made}"
                )
            if unspecified:
                lines.append(f"  文を取り出せなかった懸念: {unspecified} 件")
            return lines
        case _:
            assert_never(received)


def security_view_lines(findings: Sequence[TriagedFinding], marks: dict[str, str]) -> list[str]:
    lines = [count_heading("セキュリティの指摘", findings)]
    for triage, members in group_by_triage(findings):
        match triage.traits.placement:
            case (
                Placement.IN_ORDER
                | Placement.COLLAPSED
                | Placement.SCOPE_SECTION
                | Placement.DISMISSED_SECTION
            ):
                # 安全性の面は置き場所で形を変えず、区分の名札の見出しの下に並べる
                label = triage.traits.label
                heading = (
                    f"{label}（{len(members)} 件）" if triage.traits.heading_with_count else label
                )
                lines.append(f"[{heading}]")
            case _:
                assert_never(triage.traits.placement)
        for finding in members:
            lines.extend(finding_lines(finding, marks))
    return lines


def stopped_lines(view: StoppedView) -> list[str]:
    lines: list[str] = []
    if view.in_progress is not None:
        start = view.in_progress.record.start
        note = view.in_progress.note
        running = f"実行中（起動 {start.started_at}、プロセス {start.pid}）"
        lines.append(running if note is None else note)
    for run in view.stopped_runs:
        stopped_at = run.stopped_at or "不明"
        text = f"止まった実行（{run.kind.label}、起動 {run.start.started_at}、止めた {stopped_at}"
        if run.cost_is_unknown:
            text += "、金額は不明"
        if run.completed_cost_usd is not None:
            text += f"、終わった起動の和 {run.completed_cost_usd:.4f} USD"
        lines.append(text + "）")
    return lines
