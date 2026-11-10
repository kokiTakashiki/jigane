"""前の回の指摘を、群のプロンプトの資料にする。方針（macro）と実装（micro）が共有する。"""

from collections.abc import Collection, Sequence
from pathlib import Path
from typing import NamedTuple

from jigane.findings.discard import DISCARDED_VIEWPOINT
from jigane.findings.records import (
    FindingKind,
    HistoryLoad,
    StoredFinding,
    read_stored_state,
)
from jigane.groups.definitions import MACRO, GroupId
from jigane.reviews.selection import Held, Objected, Selection, load_selection
from jigane.skills.assembly import AssemblyInput

SECTION_HISTORY = "前の回の指摘"
NO_HISTORY_TEXT = "前の回の指摘は無い。"
NO_RELEVANT_FINDINGS_TEXT = "担当のファイルに関わる指摘なし"
# 直前の回より前の回と別のレビューの回を縮めた行の上限。超えた分は件数だけを書く。
# 回を重ねても節が直前の回の大きさとこの行数の和で止まる
OLDER_ROUND_LINE_LIMIT = 30
# 縮めた行に載せる主張の文字数。根拠の位置と異議の理由は載せない
CLAIM_HEAD_CHARS = 60
# 前の回の指摘の行を渡すシャードにだけ付ける。行が無いシャードに付けると、出し直しの規則が
# 初出の指摘にまで効き、候補が減る
OLDER_FINDINGS_RULE = (
    "- 古い回の未解決の指摘（hold と object の行）と同じ論点を出し直すときは、"
    "その形がいまのコードに残っていることを body に根拠の位置とともに示します。"
    "示せないものは出しません。"
)
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


def _listed(finding: StoredFinding, paths: Collection[str] | None) -> bool:
    """資料に載せる指摘か。直前の回の行と、古い回の縮めた行が同じ選り分けを使う。"""
    if finding.body.viewpoint == DISCARDED_VIEWPOINT:
        # 捨てた候補は資料に載せない。載せると、重さの既定の規則が出し直す指摘を nit に寄せる。
        # 方針の出力は nit を持たないため、正当な warning まで出なくなる
        return False
    # 担当の外のファイルだけを根拠にした指摘は、別のシャードの資料に載る
    return paths is None or _touches(finding, paths)


def round_lines(
    group: GroupId,
    label: str,
    findings: Sequence[StoredFinding],
    objections: dict[str, str],
    paths: Collection[str] | None,
) -> list[str]:
    lines = []
    for finding in findings:
        if not _listed(finding, paths):
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


def _unresolved_lines(
    label: str,
    findings: Sequence[StoredFinding],
    selection: Selection,
    paths: Collection[str] | None,
) -> list[str]:
    """hold と object の指摘を、一件一行の「番号 扱い 主張の先頭」にする。"""
    lines = []
    for finding in findings:
        verdict = selection.verdicts.get(finding.body.id)
        if not isinstance(verdict, Held | Objected):
            # pick は直した扱い。記録の無い指摘は判断材料にならない
            continue
        if not _listed(finding, paths):
            continue
        treatment = "hold" if isinstance(verdict, Held) else "object"
        claim = " ".join(finding.body.claim.split())[:CLAIM_HEAD_CHARS]
        lines.append(f"- {label} {finding.body.id} {treatment} {claim}")
    return lines


def _capped(lines: list[str]) -> list[str]:
    if len(lines) <= OLDER_ROUND_LINE_LIMIT:
        return lines
    # 件数の行も上限の内に数える
    shown = lines[: OLDER_ROUND_LINE_LIMIT - 1]
    rest = len(lines) - len(shown)
    return [*shown, f"- ほか {rest} 件（まだ解決していない扱いの指摘。上の行より古い）"]


class _Found(NamedTuple):
    """前の回の指摘の文に、どちらの形の行があるか。「指摘なし」だけの行は数えない。"""

    latest: bool
    older: bool


def _round_text(
    assembly: AssemblyInput,
    group: GroupId,
    paths: Collection[str] | None,
) -> tuple[str, _Found]:
    """前の回の指摘の文と、その文にある行の形。"""
    review = assembly.review
    source = assembly.source
    directory = review.id.directory()
    selection = load_selection(directory)
    lines: list[str] = []
    older: list[str] = []
    found = False
    for index, findings in enumerate(past_rounds(directory, group, source.own)):
        label = "前の回" if index == 0 else f"{index + 1} 回前"
        if index > 0:
            older.extend(_unresolved_lines(label, findings, selection, paths))
            continue
        round_found = round_lines(group, label, findings, selection.objections, paths)
        found = found or bool(round_found)
        # 同じレビューの直前の回は、何も出なかったときも行に残す。回の数え方が読み手に伝わる
        empty = f"- {label}: " + ("指摘なし" if paths is None else NO_RELEVANT_FINDINGS_TEXT)
        lines.extend(round_found or [empty])
    for related in source.related:
        other = related.review
        other_directory = other.id.directory()
        other_selection = load_selection(other_directory)
        name = f"{other.id.kind.separator}{other.id.key}"
        for index, findings in enumerate(past_rounds(other_directory, group, related.history)):
            label = f"{name} " + ("前の回" if index == 0 else f"{index + 1} 回前")
            # 別のレビューの空の回は行にしない。指摘のある行だけが判断の材料になる
            older.extend(_unresolved_lines(label, findings, other_selection, paths))
    lines.extend(_capped(older))
    return ("\n".join(lines) if lines else NO_HISTORY_TEXT), _Found(found, bool(older))


def history_text(
    assembly: AssemblyInput,
    group: GroupId,
    paths: Collection[str] | None = None,
) -> str:
    """前の回からさかのぼった群の指摘の資料。

    同じレビューの直前の回（findings.json の群）は、主張と根拠の位置の一行ずつで載せ、
    異議を付けた指摘には行の末尾に理由を添える。それより前の回（findings_history.json）と
    関連する別のレビューの回は、まだ解決していない扱い（hold と object）の指摘だけを
    「番号 扱い 主張の先頭」の一行にして新しい回から順に並べ、OLDER_ROUND_LINE_LIMIT 行で切る。
    paths を渡すと、根拠の位置がそのファイルのどれかに当たる指摘だけを載せる。
    履歴は assembly が読んだ値で、ここでは読み直さない。
    """
    return _round_text(assembly, group, paths)[0]


def shard_history_text(assembly: AssemblyInput, group: GroupId, paths: Collection[str]) -> str:
    """シャードの雛形の $history に差し込む文。指摘の行があるときだけ、出し直しの規則を続ける。"""
    text, found = _round_text(assembly, group, paths)
    if found.latest:
        return f"{text}\n\n{PREVIOUS_FINDINGS_RULES}"
    if found.older:
        # 縮めた行は根拠の位置も区分も持たないので、それを前提にする規則は掛けられない
        return f"{text}\n\n{OLDER_FINDINGS_RULE}"
    # 指摘の行が無いシャードに規則を付けると、初出の指摘にまで出し直しの条件が掛かる
    return text
