"""起動の前の入力の見積もりと、1 回の起動の上限の検査。群によらず、全ての群が共有する。"""

from dataclasses import dataclass
from typing import Any

from jigane.llm_runs import claude

LARGEST_SECTION_COUNT = 3
SECTION_HEADING_PREFIX = "### "


@dataclass(frozen=True)
class SectionSize:
    """プロンプトの一つの節の見出しと、その節のバイト数。"""

    heading: str
    bytes: int

    def as_record(self) -> dict[str, Any]:
        return {"heading": self.heading, "bytes": self.bytes}

    def text(self) -> str:
        return f"{self.heading}（{self.bytes} バイト）"


def largest_sections(prompt: str) -> list[SectionSize]:
    """プロンプトの `### ` の節を、バイト数の大きい順に三つ。"""
    sizes: dict[str, int] = {}
    heading: str | None = None
    for line in prompt.splitlines(keepends=True):
        if line.startswith(SECTION_HEADING_PREFIX):
            heading = line[len(SECTION_HEADING_PREFIX) :].strip()
            sizes.setdefault(heading, 0)
        if heading is not None:
            sizes[heading] += len(line.encode("utf-8"))
    ranked = sorted(sizes.items(), key=lambda item: item[1], reverse=True)
    return [SectionSize(name, size) for name, size in ranked[:LARGEST_SECTION_COUNT]]


# 記録の role（runs.json の起動の role）から表示名を引く
ROLE_NAMES = {
    "discovery": "発見役",
    "rebuttal": "反証役",
    "judge": "判定役",
    "alternative": "代案役",
    "defense": "擁護役",
    "themes": "束ねの役",
    "diagram": "方針の役",
}


def describe_invocation(role: str, index: int | None) -> str:
    """「発見役（シャード 2）」のように、役と、あればシャードか検証の分割の番号を文にする。"""
    name = ROLE_NAMES.get(role, role)
    if index is None:
        return name
    return f"{name}（{'シャード' if role == 'discovery' else '分割'} {index}）"


def stop_limit_text(*, reserved: bool) -> str:
    """止めの文が「達する」と言う上限の呼び名。余裕を引いて比べる群はその旨を添える。"""
    return "1 回の起動の上限から次の一往復の分を引いた額" if reserved else "1 回の起動の上限"


@dataclass(frozen=True)
class BudgetStop:
    """入力だけで上限に達する見込みのため、起動しなかった記録。"""

    # 止めた起動の記録の role（discovery、rebuttal など）。None は役の区別が無い群（方針）
    role: str | None
    # シャードや検証の分割の番号。無ければ None
    index: int | None
    tokens: int
    estimated_usd: float
    limit_usd: float
    sections: tuple[SectionSize, ...]
    notice: str | None
    # 余裕を引いた上限で検査したか。False は上限そのもので検査した
    reserved: bool

    def message(self) -> str:
        sections = "、".join(item.text() for item in self.sections)
        limit_text = stop_limit_text(reserved=self.reserved)
        prefix = "" if self.role is None else f"{describe_invocation(self.role, self.index)}: "
        return (
            f"{prefix}入力だけで {limit_text}に達する見込みのため、起動しませんでした。"
            f"入力の見積もり {self.tokens} トークン（{self.estimated_usd:.4f} USD）、"
            f"上限 {self.limit_usd:g} USD。大きい節: {sections}"
            + (f"。{self.notice}" if self.notice else "")
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "index": self.index,
            "estimated_input_tokens": self.tokens,
            "estimated_input_usd": self.estimated_usd,
            "max_budget_usd": self.limit_usd,
            "largest_sections": [item.as_record() for item in self.sections],
        }


@dataclass(frozen=True)
class BudgetCheck:
    # None なら起動してよい
    stop: BudgetStop | None
    # 表に無いモデルに最高の単価を当てたときの注。当てなければ None
    notice: str | None


def check_input_budget(
    prompt: str,
    settings: claude.RunSettings,
    *,
    role: str | None = None,
    index: int | None = None,
) -> BudgetCheck:
    """プロンプトの大きさだけの下限の見積もりで、その起動を呼んでよいかを返す。切り詰めはしない。

    settings.budget.reserve_margin が真なら、CLI に渡す上限から次の一往復の余裕を引くので
    （claude.cli_budget_usd）、引いた値が下限を割る入力も止める。偽なら、入力の費用が上限以上のときだけ止める。
    単価の表に無いモデルは、表の最高の単価で見積もって検査し、その旨の文を返す。
    """
    budget = settings.budget
    tokens = claude.estimate_input_tokens(len(prompt.encode("utf-8")))
    estimated = claude.input_cost_usd(budget.model, tokens)
    notice = (
        claude.assumed_price_notice(budget.model, estimated) if estimated.assumed_price else None
    )
    if not budget.input_stops_run(tokens):
        return BudgetCheck(None, notice)
    stop = BudgetStop(
        role,
        index,
        tokens,
        estimated.usd,
        budget.max_budget_usd,
        tuple(largest_sections(prompt)),
        notice,
        budget.reserve_margin,
    )
    return BudgetCheck(stop, notice)
