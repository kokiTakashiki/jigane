"""`jigane usage`: スキルの起動ごとの使用量と成果を、群と役の組ごとに表で出す。"""

import argparse
import csv
import json
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.output import warning_lines, warnings_json
from jigane.findings.discard import DiscardState, DiscardSummary, discard_summary
from jigane.findings.records import (
    HistoryLoad,
    HistoryWarning,
    HistoryWarningJson,
    Triage,
    TriagedFinding,
    count_by_triage,
    load_findings_state,
    load_history_checked,
)
from jigane.groups.definitions import GROUP_BY_ID, GROUP_IDS, GroupId
from jigane.llm_runs.cost import total_by_source
from jigane.llm_runs.launch_failure import StoredInvocation
from jigane.llm_runs.usage_table import ROLE_LABELS, TOTAL_ROLE, UsageField
from jigane.reviews.lifecycle import Review, load_review, resolve_review
from jigane.skills import macro, pipeline, security

_MIN_ROUNDS_TO_COMPARE = 2

# usage.csv の列の並びは、保存済みの行（USAGE_CSV の表）が頼る永続の契約なので、
# Triage の宣言順から導かずここで固定する。新しい列は既存の行がずれないよう COLUMNS の末尾に足す。
# 区分の列を後から足すときも同じで、APPENDED_TRIAGE_COLUMNS を _DEFINITIONS の末尾に置く。
# 範囲外の区分は列を持たない。tests/test_usage.py が照合する
TRIAGE_COLUMNS = (Triage.MUST_READ, Triage.REMAINING, Triage.FOLDED, Triage.REJECTED)
APPENDED_TRIAGE_COLUMNS: tuple[Triage, ...] = (Triage.UNVERIFIED,)
ALL_TRIAGE_COLUMNS = TRIAGE_COLUMNS + APPENDED_TRIAGE_COLUMNS

# 表の「捨てた割合」の列と回ごとの行の文を、状態ごとに決める
DISCARD_STATE_TEXT = {
    DiscardState.UNRECORDED: "不明",
    DiscardState.NONE_DISCOVERED: "発見なし",
}


@dataclass(frozen=True)
class Measures:
    """役の行と合計の行が共に持つ測定値。行の種類は RoleRow と TotalRow が分ける。"""

    group: str
    model: str | None
    effort: str | None
    calls: int
    turns: int
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_usd: float
    duration_s: float

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


@dataclass(frozen=True)
class RoleRow(Measures):
    """群と役の組ごとの行。プロンプトの版と文字数は、合計の行には無い。"""

    role: str
    prompt_version: str | None
    prompt_chars: int | None


@dataclass(frozen=True)
class TotalRow(Measures):
    """群の合計の行。区分の数と、群によっては捨てた割合、MCP を持つ。

    substituted_cost_usd は金額が報告されず上限の額で代えた分。discard_rounds は
    方針の群だけが持ち、古い回から新しい回の順で、最後が最新の実行である。mcp は
    セキュリティの群だけが持つ。持たない群は None で、JSON にその鍵を出さない。
    """

    findings_total: int
    triage_counts: Mapping[Triage, int]
    usd_per_must_read: float | None
    considered: DiscardSummary | None
    substituted_cost_usd: float
    discard_rounds: tuple[tuple[str | None, DiscardSummary], ...] | None
    mcp: str | None


# 行の種類は閉じた二つで、共通の測定値は Measures が持つ。Measures は和に含めない
UsageRow = RoleRow | TotalRow


def _only_role(read: Callable[[RoleRow], object]) -> Callable[[UsageRow], object]:
    """役ごとの行だけが持つ列の読み方。合計の行では空になる。"""
    return lambda row: read(row) if isinstance(row, RoleRow) else None


def _only_total(read: Callable[[TotalRow], object]) -> Callable[[UsageRow], object]:
    """合計の行だけが持つ列の読み方。役ごとの行では空になる。"""
    return lambda row: read(row) if isinstance(row, TotalRow) else None


def _considered(read: Callable[[DiscardSummary], object]) -> Callable[[UsageRow], object]:
    return _only_total(lambda row: None if row.considered is None else read(row.considered))


def rounded_ratio(summary: DiscardSummary) -> float | None:
    return None if summary.ratio is None else round(summary.ratio, 3)


class Display(Enum):
    """列をどの出力に出すか。JSON と CSV は表の --wide と同じ列を出す。"""

    ALWAYS = "always"
    COMPACT_ONLY = "compact_only"
    WIDE_ONLY = "wide_only"


@dataclass(frozen=True)
class Column:
    """表と CSV と JSON が共有する列。名前は CSV と JSON の鍵で、heading は人が読む表の見出し。

    display は既定の表（compact）と、--wide、--json、--csv のどれに出すかを決める。
    """

    name: str
    heading: str
    read: Callable[[UsageRow], object]
    text: bool = False
    display: Display = Display.ALWAYS


def _field(
    name: str, heading: str, *, text: bool = False, display: Display = Display.ALWAYS
) -> Column:
    return Column(name, heading, lambda row: getattr(row, name), text, display)


def _role_field(
    name: str, heading: str, *, text: bool = False, display: Display = Display.ALWAYS
) -> Column:
    return Column(name, heading, _only_role(lambda row: getattr(row, name)), text, display)


def _triage_column(triage: Triage) -> Column:
    return Column(
        triage.value,
        triage.traits.label,
        _only_total(lambda row: row.triage_counts.get(triage)),
        display=Display.ALWAYS if triage.traits.read_first else Display.WIDE_ONLY,
    )


# CSV と JSON の列名は英語で固定し、人が読む表の見出しだけ日本語にする。
_GROUP = _field(UsageField.GROUP, "群", text=True)
_ROLE = Column(
    UsageField.ROLE,
    "役",
    lambda row: row.role if isinstance(row, RoleRow) else TOTAL_ROLE,
    text=True,
)
_FINDINGS_TOTAL = Column(
    UsageField.FINDINGS_TOTAL, "指摘", _only_total(lambda row: row.findings_total)
)
_USD_PER_MUST_READ = Column(
    UsageField.USD_PER_MUST_READ,
    "必読1件あたりUSD",
    _only_total(lambda row: row.usd_per_must_read),
)
# 方針の群の合計の行だけが持つ、検討した候補の列。状態は discard_state が持ち、数の列は
# 状態が MEASURED のときだけ埋まる。表では状態の列は割合の列の文に溶ける
_DISCARD_RATIO = Column(UsageField.DISCARD_RATIO, "捨てた割合", _considered(rounded_ratio))
_CONSIDERED_COLUMNS = (
    Column(UsageField.DISCOVERED, "発見", _considered(lambda summary: summary.discovered)),
    Column(UsageField.DISCARDED, "捨てた", _considered(lambda summary: summary.discarded)),
    _DISCARD_RATIO,
    Column(
        UsageField.DISCARD_STATE,
        "捨てた割合の状態",
        _considered(lambda summary: summary.state.value),
        display=Display.WIDE_ONLY,
    ),
)

# usage.csv の列の並びは保存済みの行が頼る永続の契約で、先頭の並びを変えない（上の定数の注）。
# 全出力の列をこの一つの並びで定義し、compact と wide の出し分けは各列の display が決める。
# 既定の表は端末の幅に収めるため、トークンは 4 種の合計を 1 列で出す
_DEFINITIONS = (
    _GROUP,
    _ROLE,
    _field(UsageField.MODEL, "モデル", text=True, display=Display.WIDE_ONLY),
    _role_field(UsageField.PROMPT_VERSION, "プロンプト版", text=True),
    _role_field(UsageField.PROMPT_CHARS, "文字数", display=Display.WIDE_ONLY),
    _field(UsageField.CALLS, "起動"),
    _field(UsageField.TOTAL_TOKENS, "トークン合計", display=Display.COMPACT_ONLY),
    _field(UsageField.TURNS, "ターン", display=Display.WIDE_ONLY),
    _field(UsageField.INPUT_TOKENS, "入力", display=Display.WIDE_ONLY),
    _field(UsageField.CACHE_CREATION_TOKENS, "キャッシュ作成", display=Display.WIDE_ONLY),
    _field(UsageField.CACHE_READ_TOKENS, "キャッシュ読出", display=Display.WIDE_ONLY),
    _field(UsageField.OUTPUT_TOKENS, "出力", display=Display.WIDE_ONLY),
    _field(UsageField.COST_USD, "金額USD"),
    _field(UsageField.DURATION_S, "秒", display=Display.WIDE_ONLY),
    _FINDINGS_TOTAL,
    *(_triage_column(t) for t in TRIAGE_COLUMNS),
    _USD_PER_MUST_READ,
    _field(UsageField.EFFORT, "effort", text=True, display=Display.WIDE_ONLY),
    *_CONSIDERED_COLUMNS,
    *(_triage_column(t) for t in APPENDED_TRIAGE_COLUMNS),
)
COLUMNS = tuple(c for c in _DEFINITIONS if c.display is not Display.COMPACT_ONLY)
COMPACT_COLUMNS = tuple(c for c in _DEFINITIONS if c.display is not Display.WIDE_ONLY)


def row_json(row: UsageRow) -> dict[str, Any]:
    """--json の rows の一行。列の順に、合計の行だけが持つ欄を続ける。"""
    document: dict[str, Any] = {column.name: column.read(row) for column in COLUMNS}
    if isinstance(row, TotalRow):
        document["substituted_cost_usd"] = row.substituted_cost_usd
        if row.discard_rounds is not None:
            document["discard_rounds"] = [
                {
                    "ran_at": ran_at,
                    "discard_state": summary.state.value,
                    "discovered": summary.discovered,
                    "discarded": summary.discarded,
                    "discard_ratio": rounded_ratio(summary),
                }
                for ran_at, summary in row.discard_rounds
            ]
        if row.mcp is not None:
            document["mcp"] = row.mcp
    return document


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    usage = subparsers.add_parser("usage", help="スキルの使用量と成果を表で示します")
    usage.add_argument("review")
    form = usage.add_mutually_exclusive_group()
    form.add_argument("--json", action="store_true")
    form.add_argument("--csv", action="store_true")
    usage.add_argument(
        "--wide",
        action="store_true",
        help="表に全ての列を出します。--json と --csv は常に全列です",
    )
    usage.add_argument("--no-header", action="store_true", help="--csv で見出しの行を出しません")
    usage.set_defaults(handler=run)


def _usage_row(
    group: GroupId,
    role: str,
    model: str | None,
    effort: str | None,
    version: str | None,
    *,
    items: list[StoredInvocation],
) -> RoleRow:
    chars = [item.stamp.chars for item in items if item.stamp is not None]
    return RoleRow(
        group=GROUP_BY_ID[group].label,
        role=ROLE_LABELS[role],
        model=model,
        effort=effort,
        prompt_version=version,
        # 起動ごとに文字数は違う（候補の数や分割で変わる）ので、平均を出す。
        prompt_chars=round(sum(chars) / len(chars)) if chars else None,
        calls=len(items),
        turns=sum(item.record.num_turns for item in items),
        input_tokens=sum(item.record.input_tokens for item in items),
        cache_creation_tokens=sum(item.record.cache_creation_input_tokens for item in items),
        cache_read_tokens=sum(item.record.cache_read_input_tokens for item in items),
        output_tokens=sum(item.record.output_tokens for item in items),
        cost_usd=round(sum(total_by_source([item.record.cost]).usd for item in items), 4),
        duration_s=round(sum(item.record.duration_ms for item in items) / 1000, 1),
    )


@dataclass(frozen=True)
class GroupTally:
    """一つの群の合計の行を作るために、役ごとの行と一緒に集めた値。"""

    model: str | None
    effort: str | None
    rows: Sequence[RoleRow]
    findings: Sequence[TriagedFinding]
    group_run: pipeline.GroupRun
    history: HistoryLoad

    @property
    def elapsed_s(self) -> float | None:
        return self.group_run.elapsed_s


def _total_row(group: GroupId, tally: GroupTally) -> TotalRow:
    rows = tally.rows
    # 区分は読み込みで補い済みの値を数える。
    counts = count_by_triage(tally.findings)
    triage_counts = {column: counts[column] for column in ALL_TRIAGE_COLUMNS}
    cost_usd = round(sum(row.cost_usd for row in rows), 4)
    # 必読1件あたりの金額の「必読」は、関門を塞ぐ区分
    must_read = sum(triage_counts[t] for t in ALL_TRIAGE_COLUMNS if t.traits.read_first)
    considered = discard_rounds = None
    if group == macro.GROUP:
        considered = discard_summary(tally.group_run.considered)
        past = [
            (entry.ran_at, entry.discard) for entry in tally.history.rounds.get(macro.GROUP, [])
        ]
        discard_rounds = (*past, (tally.group_run.ran_at, considered))
    return TotalRow(
        group=GROUP_BY_ID[group].label,
        model=tally.model,
        effort=tally.effort,
        calls=sum(row.calls for row in rows),
        turns=sum(row.turns for row in rows),
        input_tokens=sum(row.input_tokens for row in rows),
        cache_creation_tokens=sum(row.cache_creation_tokens for row in rows),
        cache_read_tokens=sum(row.cache_read_tokens for row in rows),
        output_tokens=sum(row.output_tokens for row in rows),
        cost_usd=cost_usd,
        # 発見役とシャードは並行に動くため、役ごとの秒の合算は経過時間より大きい。
        # 経過時間を記録する前の実行にはそれが無いので、合算で代える。
        duration_s=(
            tally.elapsed_s
            if tally.elapsed_s is not None
            else round(sum(row.duration_s for row in rows), 1)
        ),
        findings_total=len(tally.findings),
        triage_counts=triage_counts,
        usd_per_must_read=round(cost_usd / must_read, 4) if must_read else None,
        considered=considered,
        substituted_cost_usd=round(
            total_by_source(pipeline.invocation_costs(tally.group_run)).limit_substitute, 4
        ),
        # 回ごとの並びは、runs.json が最新の実行しか持たないため findings_history.json の回で補う
        discard_rounds=discard_rounds,
        mcp=pipeline.mcp_label(tally.group_run) if group == security.GROUP else None,
    )


@dataclass(frozen=True)
class UsageReport:
    """usage の集計の結果。行と履歴の警告を一組にして、表、JSON、CSV の出力が同じ型だけを読む。

    警告は壊れた履歴を読めなかった分。描画は形ごとに違う（表は先頭の行、JSON は
    history_warnings の欄、CSV は追記用なので出さない）が、警告の足し方はここを通す。
    """

    rows: Sequence[UsageRow]
    warnings: tuple[HistoryWarning, ...]

    def warning_lines(self) -> list[str]:
        return warning_lines(self.warnings)

    def warnings_json(self) -> list[HistoryWarningJson]:
        return warnings_json(self.warnings)


def usage_report(review: Review) -> UsageReport:
    # 壊れた履歴は回が空になり、警告が残る
    history = load_history_checked(review.id.directory())
    return UsageReport(_usage_rows(review, history), history.warnings)


def _usage_rows(review: Review, history: HistoryLoad) -> list[UsageRow]:
    directory = review.id.directory()
    runs = pipeline.read_runs(directory)
    state = load_findings_state(directory)
    rows: list[UsageRow] = []
    for group in GROUP_IDS:
        if group not in runs:
            continue
        model = runs[group].model
        # effort を記録する前の実行には無い
        effort = runs[group].effort
        buckets: dict[tuple[str | None, str | None], list[StoredInvocation]] = defaultdict(list)
        for item in pipeline.all_invocations(runs[group]):
            buckets[(item.role, None if item.stamp is None else item.stamp.version)].append(item)
        group_rows = [
            _usage_row(group, role, model, effort, version, items=items)
            for role in ROLE_LABELS
            for (bucket_role, version), items in sorted(
                buckets.items(), key=lambda entry: entry[0][1] or ""
            )
            if bucket_role == role
        ]
        rows += group_rows
        rows.append(
            _total_row(
                group,
                GroupTally(
                    model=model,
                    effort=effort,
                    rows=tuple(group_rows),
                    findings=tuple(state.groups.get(group, [])),
                    group_run=runs[group],
                    history=history,
                ),
            )
        )
    return rows


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def _table_cell(row: UsageRow, column: Column) -> str:
    if not isinstance(row, TotalRow) or column is not _DISCARD_RATIO or row.considered is None:
        return _cell(column.read(row))
    summary = row.considered
    return DISCARD_STATE_TEXT.get(summary.state) or f"{rounded_ratio(summary):.0%}"


def round_lines(report: UsageReport) -> list[str]:
    """方針の回ごとの捨てた割合を、古い回から新しい回の順に出す。回が一つ以下なら空。

    一つだけの回は、合計の行の「捨てた割合」と同じ値なので繰り返さない。
    """
    rounds = [
        r for row in report.rows if isinstance(row, TotalRow) for r in row.discard_rounds or ()
    ]
    if len(rounds) < _MIN_ROUNDS_TO_COMPARE:
        return []
    lines = ["", "方針の回ごとの捨てた割合（古い回から）"]
    for ran_at, summary in rounds:
        counts = f"発見 {summary.discovered}、捨てた {summary.discarded}"
        text = DISCARD_STATE_TEXT.get(summary.state) or f"{rounded_ratio(summary):.0%}（{counts}）"
        lines.append(f"  {ran_at or '時刻不明'}  {text}")
    return lines


def mcp_lines(report: UsageReport) -> list[str]:
    """セキュリティの群で使った MCP を示す。CSV の列は変えない。"""
    return [
        f"{row.group}の{row.mcp}" for row in report.rows if isinstance(row, TotalRow) and row.mcp
    ]


def substitute_lines(report: UsageReport) -> list[str]:
    """金額が報告されず上限の額で代えた推定の分を、群ごとに示す。代えた起動が無ければ空。"""
    return [
        f"{row.group}の合計 {row.cost_usd:.4f} USD のうち推定（上限の額で代用） "
        f"{row.substituted_cost_usd:.4f} USD"
        for row in report.rows
        if isinstance(row, TotalRow) and row.substituted_cost_usd
    ]


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int, *, right: bool) -> str:
    gap = " " * (width - _width(text))
    return gap + text if right else text + gap


def table_lines(report: UsageReport, *, wide: bool = False) -> list[str]:
    columns = COLUMNS if wide else COMPACT_COLUMNS
    cells = [[column.heading for column in columns]] + [
        [_table_cell(row, column) for column in columns] for row in report.rows
    ]
    widths = [max(_width(line[i]) for line in cells) for i in range(len(columns))]
    return [
        "  ".join(
            _pad(text, widths[i], right=not columns[i].text) for i, text in enumerate(line)
        ).rstrip()
        for line in cells
    ]


def write_csv(report: UsageReport, *, header: bool) -> None:
    writer = csv.writer(sys.stdout, lineterminator="\n")
    if header:
        writer.writerow([column.name for column in COLUMNS])
    for row in report.rows:
        writer.writerow([_cell(column.read(row)) for column in COLUMNS])


def run(args: argparse.Namespace, context: Context) -> int:
    del context
    review = load_review(resolve_review(args.review))
    report = usage_report(review)
    if args.json:
        document = {
            "rows": [row_json(row) for row in report.rows],
            "history_warnings": report.warnings_json(),
        }
        print(json.dumps(document, ensure_ascii=False, indent=2))
    elif args.csv:
        # CSV は追記用なので、警告を混ぜず行だけを出す
        write_csv(report, header=not args.no_header)
    else:
        lines = (
            table_lines(report, wide=args.wide)
            + round_lines(report)
            + mcp_lines(report)
            + substitute_lines(report)
        )
        print("\n".join([*report.warning_lines(), *lines]))
    return EXIT_OK
