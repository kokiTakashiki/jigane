"""設計書の形の見込みの総額。入力の費用に、出力と推論の分を倍率で足す。

設計書の形は推論の出力が費用の大半を占め、入力だけの見積もりでは 1 回の上限との比較にならない。
出力の単価の表は持たず、過去の実額と入力の費用の比（倍率）で見込む。
"""

import csv
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jigane.groups.definitions import GROUP_BY_ID, MACRO
from jigane.groups.policy_runs import PolicyRole
from jigane.llm_runs import claude
from jigane.llm_runs.usage_table import INPUT_SIDE_TOKEN_FIELDS, USAGE_CSV, UsageField

# 過去の実額がどこにも無いときの倍率の仮置き。測定の結果が出たら置き換える
DEFAULT_OUTPUT_MULTIPLIER = 6.0


class MultiplierSource(StrEnum):
    USAGE_CSV = "usage_csv"
    PAST_RUN = "past_run"
    DEFAULT = "default"


class SkipReason(StrEnum):
    """使用量の表の行を倍率の根拠にしなかった理由。"""

    BROKEN_COLUMNS = "broken_columns"
    EMPTY_LABEL = "empty_label"
    INVALID_VALUE = "invalid_value"

    @property
    def label(self) -> str:
        return _SKIP_REASON_LABELS[self]


_SKIP_REASON_LABELS = {
    SkipReason.BROKEN_COLUMNS: "欄の数が合わない",
    SkipReason.EMPTY_LABEL: "group か role が空の",
    SkipReason.INVALID_VALUE: "設計書の行の値が欠けるか数でない",
}


@dataclass(frozen=True)
class SkippedRows:
    """倍率の根拠にしなかった使用量の表の行の、理由ごとの件数。"""

    # 件数が 0 の理由は持たない。並びは SkipReason の定義順
    counts: tuple[tuple[SkipReason, int], ...] = ()

    @classmethod
    def from_counter(cls, counter: Counter[SkipReason]) -> "SkippedRows":
        return cls(tuple((reason, counter[reason]) for reason in SkipReason if counter[reason]))

    @property
    def total(self) -> int:
        return sum(count for _, count in self.counts)


@dataclass(frozen=True)
class OutputEstimate:
    input_usd: float
    multiplier: float
    source: MultiplierSource
    # 倍率の根拠にしなかった使用量の表の行の、理由ごとの数。倍率の値には効かない
    skipped: SkippedRows = SkippedRows()

    @property
    def skipped_usage_rows(self) -> int:
        return self.skipped.total

    @property
    def total_usd(self) -> float:
        return self.input_usd * self.multiplier

    def as_record(self) -> dict[str, Any]:
        return {
            "total_usd": self.total_usd,
            "output_multiplier": self.multiplier,
            "output_multiplier_source": self.source.value,
            # 壊れた行が無いときは鍵ごと出さず、群ごとの鍵の集合を増やさない
            **({"skipped_usage_rows": self.skipped_usage_rows} if self.skipped_usage_rows else {}),
        }


def _ratio(model: str, cost_usd: float, input_tokens: int) -> float | None:
    """実額と、同じ入力を入力の単価で数えた費用の比。0 で割る行は数えない。"""
    input_usd = claude.input_cost_usd(model, input_tokens).usd
    if cost_usd <= 0 or input_usd <= 0:
        return None
    return cost_usd / input_usd


@dataclass(frozen=True)
class UsageCsvReading:
    """使用量の表から読んだ倍率と、定義と合わずに倍率の根拠にしなかった行の数。"""

    multiplier: float | None
    skipped: SkippedRows = SkippedRows()


def _broken_reason(row: dict[str, str | None]) -> SkipReason | None:
    """CSV の欄が足りないか多すぎる行、または group か role が空の行の理由。壊れていなければ None。

    知らない群や役の語は壊れた行とみなさない。過去の群や廃止した役の名前の行は有効な古い記録のため。
    欄が多すぎる行は、csv.DictReader が余った値を None の鍵にまとめる。
    """
    if None in row or any(value is None for value in row.values()):
        return SkipReason.BROKEN_COLUMNS
    if not row.get(UsageField.GROUP) or not row.get(UsageField.ROLE):
        return SkipReason.EMPTY_LABEL
    return None


def _row_ratio(row: dict[str, str | None]) -> float | None:
    """設計書の行の倍率。列の欠けと数でない欄は ValueError になる。"""
    model = row.get(UsageField.MODEL)
    cost = row.get(UsageField.COST_USD)
    if model is None or cost is None:
        raise ValueError("model or cost_usd is missing")
    tokens = 0
    for field in INPUT_SIDE_TOKEN_FIELDS:
        value = row.get(field)
        if value is None:
            raise ValueError(f"{field} is missing")
        tokens += int(value)
    return _ratio(model, float(cost), tokens)


def read_usage_csv(repository_root: Path) -> UsageCsvReading:
    """使用量の表の「方針,設計書」の行の倍率の平均と、定義と合わない行の数。

    表が無い、該当の行が無いときの倍率は None。壊れた行と、設計書の行の欠けた値は数える。
    """
    try:
        with (repository_root / USAGE_CSV).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return UsageCsvReading(None)
    design_group = GROUP_BY_ID[MACRO].label
    design_role = PolicyRole.DESIGN.label
    ratios: list[float] = []
    skipped: Counter[SkipReason] = Counter()
    for row in rows:
        reason = _broken_reason(row)
        if reason is not None:
            skipped[reason] += 1
            continue
        if row[UsageField.GROUP] != design_group or row[UsageField.ROLE] != design_role:
            # 設計書の行でない行は倍率の計算に使わない。知らない語の行もここで読み飛ばす
            continue
        try:
            ratio = _row_ratio(row)
        except ValueError:
            skipped[SkipReason.INVALID_VALUE] += 1
            continue
        if ratio is not None:
            ratios.append(ratio)
    return UsageCsvReading(
        sum(ratios) / len(ratios) if ratios else None, SkippedRows.from_counter(skipped)
    )


def skipped_rows_message(skipped: SkippedRows) -> str | None:
    """倍率の根拠にしなかった行があるときの、理由別の警告の文。無ければ None。"""
    if skipped.total == 0:
        return None
    breakdown = "、".join(f"{reason.label} {count} 行" for reason, count in skipped.counts)
    path = USAGE_CSV.as_posix()
    return f"警告: {path} の {skipped.total} 行を倍率の根拠にしませんでした（{breakdown}）"


def past_run_multiplier(past_run: dict[str, Any] | None) -> float | None:
    """runs.json の設計書の形の過去の実額（pipeline.past_run）の倍率。無いときは None。"""
    if past_run is None or not past_run["model"]:
        return None
    return _ratio(past_run["model"], past_run["cost_usd"], past_run["input_tokens"])


def estimate_output(
    repository_root: Path, past_run: dict[str, Any] | None, input_usd: float
) -> OutputEstimate:
    """倍率は、使用量の表、この審査の過去の実額、既定の倍率の順に引く。"""
    reading = read_usage_csv(repository_root)
    skipped = reading.skipped
    if reading.multiplier is not None:
        return OutputEstimate(input_usd, reading.multiplier, MultiplierSource.USAGE_CSV, skipped)
    from_run = past_run_multiplier(past_run)
    if from_run is not None:
        return OutputEstimate(input_usd, from_run, MultiplierSource.PAST_RUN, skipped)
    return OutputEstimate(input_usd, DEFAULT_OUTPUT_MULTIPLIER, MultiplierSource.DEFAULT, skipped)


def warning_message(estimate: OutputEstimate, limit_usd: float) -> str:
    return (
        "警告: 出力と推論の分を含めた見込みが 1 回の起動の目安に達します。目安を超えて続けます。"
        f"入力 {estimate.input_usd:.4f} USD に倍率 {estimate.multiplier:.1f}"
        f"（{estimate.source.value}）を掛けた見込み {estimate.total_usd:.4f} USD、"
        f"目安 {limit_usd:g} USD"
    )
