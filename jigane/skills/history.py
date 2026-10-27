"""前の回の指摘を、群のプロンプトの資料にする。方針（macro）と実装（micro）が共有する。"""

from collections.abc import Collection, Sequence
from pathlib import Path

from jigane.findings.discard import DISCARDED_VIEWPOINT
from jigane.findings.records import (
    FindingKind,
    HistoryLoad,
    StoredFinding,
    read_stored_state,
)
from jigane.groups.definitions import MACRO, GroupId
from jigane.reviews.selection import load_selection
from jigane.skills.assembly import AssemblyInput

SECTION_HISTORY = "前の回の指摘"
NO_HISTORY_TEXT = "前の回の指摘は無い。"
NO_RELEVANT_FINDINGS_TEXT = "担当のファイルに関わる指摘なし"
# 前の回の指摘の行を渡すシャードにだけ付ける。行が無いシャードに付けると、出し直しの規則が
# 初出の指摘にまで効き、候補が減る
PREVIOUS_FINDINGS_RULES = (
    "- 「前の回の指摘」にあるものと同じ論点を出し直すときは、その行の根拠の位置にあった形が、"
    "いまのコードに同じ形で残っていることを body に示します。示せないものは直ったので出しません。"
    "\n"
    "- 前の回の指摘の直し方そのものへの新しい候補（一段深い縁の場合）は、この変更より前からある"
    "振る舞いを崩したときだけ出します。それ以外は出しません。"
    "\n"
    "- 区分が未検証の論点は、直ったとみなしません。同じ形が残っていれば出し直します。"
)


def _attributes(group: GroupId, finding: StoredFinding) -> str:
    """行の重さの後ろに添える値。同じ論点を出し直すとき、前の回の重さを既定にできる値を載せる。

    方針の行には区分と、深掘りなら deepens と印も載せる。実装の行は変えない。
    """
    if group == MACRO:
        # 方針の重さは severity と範囲内かで決まる。検証を行わないので反証の判定は無い
        if finding.body.kind is FindingKind.SCOPE:
            return "in_scope=false"
        # 深掘りは区分が残りになり、必読に数えない。行から見えないと、直されていない必読と読まれる
        triage = finding.triage
        parts = ["in_scope=true", f"区分={'なし' if triage is None else triage.traits.label}"]
        continued_from = finding.body.continued_from_value()
        if continued_from is not None:
            parts += [f"deepens={continued_from}", "深掘り（必読に数えない）"]
        return " ".join(parts)
    triage = finding.triage
    adjudication = finding.body.adjudication
    verdict = "なし" if adjudication is None else adjudication.verdict.value
    label = "なし" if triage is None else triage.traits.label
    return f"区分={label} 反証={verdict}"


def _touches(finding: StoredFinding, paths: Collection[str]) -> bool:
    return any(location["path"] in paths for location in finding.body.extra["evidence"])


def round_lines(
    group: GroupId,
    label: str,
    findings: Sequence[StoredFinding],
    objections: dict[str, str],
    paths: Collection[str] | None,
) -> list[str]:
    lines = []
    for finding in findings:
        if finding.body.viewpoint == DISCARDED_VIEWPOINT:
            # 捨てた候補は資料に載せない。載せると、重さの既定の規則が出し直す指摘を nit に寄せる。
            # 方針の出力は nit を持たないため、正当な warning まで出なくなる
            continue
        if paths is not None and not _touches(finding, paths):
            # 担当の外のファイルだけを根拠にした指摘は、別のシャードの資料に載る
            continue
        places = "、".join(
            f"{e['path']}:{e['start_line']}" for e in finding.body.extra["evidence"][:2]
        )
        line = (
            f"- {label} {finding.body.id} {finding.body.viewpoint} {finding.body.severity} "
            f"{_attributes(group, finding)} {finding.body.claim}（{places}）"
        )
        reason = objections.get(finding.body.id)
        if reason is not None:
            line += f"（利用者の異議: {' '.join(reason.split())}）"
        lines.append(line)
    return lines


def past_rounds(
    directory: Path, group: GroupId, history: HistoryLoad
) -> list[Sequence[StoredFinding]]:
    """直前の回から古い回へ。群を一度も実行していなければ空。"""
    latest = read_stored_state(directory).groups.get(group)
    older = [entry.findings for entry in reversed(history.rounds.get(group, []))]
    if not latest and not any(older):
        return []
    return [latest or [], *older]


def _round_text(
    assembly: AssemblyInput,
    group: GroupId,
    paths: Collection[str] | None,
) -> tuple[str, bool]:
    """前の回の指摘の文と、指摘の行が一つでもあるか。「指摘なし」だけの行は指摘に数えない。"""
    review = assembly.review
    source = assembly.source
    directory = review.id.directory()
    objections = load_selection(directory).objections
    lines: list[str] = []
    found = False
    for index, findings in enumerate(past_rounds(directory, group, source.own)):
        label = "前の回" if index == 0 else f"{index + 1} 回前"
        round_found = round_lines(group, label, findings, objections, paths)
        found = found or bool(round_found)
        # 同じレビューでは、何も出なかった回も行に残す。回の数え方が読み手に伝わる
        empty = f"- {label}: " + ("指摘なし" if paths is None else NO_RELEVANT_FINDINGS_TEXT)
        lines.extend(round_found or [empty])
    for related in source.related:
        other = related.review
        other_directory = other.id.directory()
        other_objections = load_selection(other_directory).objections
        name = f"{other.id.kind.separator}{other.id.key}"
        for index, findings in enumerate(past_rounds(other_directory, group, related.history)):
            label = f"{name} " + ("前の回" if index == 0 else f"{index + 1} 回前")
            # 別のレビューの空の回は行にしない。指摘のある行だけが判断の材料になる
            related_found = round_lines(group, label, findings, other_objections, paths)
            found = found or bool(related_found)
            lines.extend(related_found)
    return ("\n".join(lines) if lines else NO_HISTORY_TEXT), found


def history_text(
    assembly: AssemblyInput,
    group: GroupId,
    paths: Collection[str] | None = None,
) -> str:
    """前の回からさかのぼった群の指摘を、回ごとに主張と根拠の位置の一行ずつにしたもの。

    同じレビューの回（直前は findings.json の群、それ以前は findings_history.json）の後に、
    関連する別のレビューの回を新しいレビューから順に並べる。別のレビューの行には @名前を添える。
    利用者が異議を付けた指摘には、行の末尾に理由を添える。
    paths を渡すと、根拠の位置がそのファイルのどれかに当たる指摘だけを載せる。
    履歴は assembly が読んだ値で、ここでは読み直さない。
    """
    return _round_text(assembly, group, paths)[0]


def shard_history_text(assembly: AssemblyInput, group: GroupId, paths: Collection[str]) -> str:
    """シャードの雛形の $history に差し込む文。指摘の行があるときだけ、出し直しの規則を続ける。"""
    text, found = _round_text(assembly, group, paths)
    if found:
        return f"{text}\n\n{PREVIOUS_FINDINGS_RULES}"
    # 指摘の行が無いシャードに規則を付けると、初出の指摘にまで出し直しの条件が掛かる
    return text
