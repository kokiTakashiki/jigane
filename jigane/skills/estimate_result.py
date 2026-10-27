"""見積もりの結果。群ごとの型の和で、共通の欄は基底に、群だけの欄は各型に置く。

確認の画面、`jigane estimate` の text と --json は、この型から読む。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude
from jigane.skills.input_budget import SectionSize, stop_limit_text
from jigane.skills.output_estimate import OutputEstimate, warning_message


@dataclass(frozen=True)
class InputCostEstimate:
    """入力の費用の欄。確認の画面と --json は、ここから読む。"""

    cost: claude.InputCost
    exceeds_budget: bool

    def as_record(self) -> dict[str, Any]:
        return {
            "input_cost_usd": self.cost.usd,
            "input_price_usd_per_million_tokens": self.cost.price_per_million,
            "input_price_assumed": self.cost.assumed_price,
            "exceeds_budget": self.exceeds_budget,
        }


@dataclass(frozen=True)
class ExpectedInputCost(InputCostEstimate):
    """出力と推論の分を含めた見込みを持つ入力の費用。見込みと上限の比べも持つ。"""

    expected: OutputEstimate
    exceeds_budget_expected: bool


@dataclass(frozen=True)
class ShardedInputCost(InputCostEstimate):
    """シャード単位で上限と比べる群の入力の費用。シャードごとの費用の欄を足す。"""

    shard_cost_usd: tuple[float, ...]

    def as_record(self) -> dict[str, Any]:
        return {**super().as_record(), "shard_input_cost_usd": list(self.shard_cost_usd)}


@dataclass(frozen=True)
class EstimateCommon(ABC):
    discovery_input_tokens: int
    # 記録にある前回の実行の実測。記録が無ければ None
    past_run: dict[str, Any] | None
    note: str

    @property
    @abstractmethod
    def discovery_calls(self) -> int:
        """発見役を起動する回数。"""

    @property
    @abstractmethod
    def has_verifier(self) -> bool:
        """検証役を持つか。持つ群の検証役の回数は指摘の数で決まり、事前には分からない。"""

    @abstractmethod
    def policy_shape(self) -> PolicyShape | None:
        """設計書の形。形を持つのは方針だけで、他は None。"""

    @abstractmethod
    def expected_output(self) -> OutputEstimate | None:
        """出力と推論の分を含めた見込み。持たない型は None。"""

    @abstractmethod
    def policy_notice_text(self) -> str | None:
        """方針のレビューについての注。持たない型は None。"""

    @abstractmethod
    def largest_section_entries(self) -> tuple[SectionSize, ...]:
        """プロンプトの大きい節。節ごとの大きさを持たない型は空。"""

    @abstractmethod
    def input_cost(self, basis: claude.RunBudget) -> InputCostEstimate | None:
        """入力の費用。この型が持たなければ None。"""

    def expected_total_usd(self) -> float | None:
        expected = self.expected_output()
        return None if expected is None else expected.total_usd

    def input_cost_record(self, basis: claude.RunBudget) -> dict[str, Any]:
        """入力の費用の欄。to_record の欄に足して使う。"""
        cost = self.input_cost(basis)
        if cost is None:
            return {}
        stop_warning = self.budget_stop_warning(basis)
        expected = self.expected_record(basis)
        # 警告が無いときは鍵ごと出さず、群ごとの鍵の集合を増やさない
        return {
            **cost.as_record(),
            **({} if expected is None else {"expected": expected}),
            **({} if stop_warning is None else {"budget_stop_warning": stop_warning}),
        }

    def expected_record(self, basis: claude.RunBudget) -> dict[str, Any] | None:
        """--json の入れ子 `expected`。見込みを持つ型（設計書の形）だけが返し、他は None。"""
        cost = self.input_cost(basis)
        if not isinstance(cost, ExpectedInputCost):
            return None
        warning = self.expected_budget_warning(basis)
        return {
            **cost.expected.as_record(),
            "exceeds_budget": cost.exceeds_budget_expected,
            **({} if warning is None else {"budget_warning": warning}),
        }

    def budget_stop_warning(self, basis: claude.RunBudget) -> str | None:
        """入力だけで実行が止まるときの文。text と --json はここから読む。"""
        cost = self.input_cost(basis)
        if cost is None or not cost.exceeds_budget:
            return None
        limit = stop_limit_text(reserved=basis.reserve_margin)
        upper = f"上限 {basis.max_budget_usd:g} USD"
        return f"入力だけで {limit}（{upper}）に達する見込みのため、実行は止まります。"

    def expected_budget_warning(self, basis: claude.RunBudget) -> str | None:
        """見込みが目安に達するときの警告の文。text と --json はここから読む。"""
        cost = self.input_cost(basis)
        if not isinstance(cost, ExpectedInputCost) or not cost.exceeds_budget_expected:
            return None
        return warning_message(cost.expected, basis.max_budget_usd)

    def to_record(self) -> dict[str, Any]:
        """--json に渡す鍵と値。共通の鍵は型ごとの鍵の後ろに、ここだけで組む。"""
        return {
            **self._specific_record(),
            "discovery_input_tokens": self.discovery_input_tokens,
            "past_run": self.past_run,
            "note": self.note,
        }

    @abstractmethod
    def _specific_record(self) -> dict[str, Any]:
        """型だけが持つ鍵と値。"""


@dataclass(frozen=True)
class PolicyEstimateBase(EstimateCommon, ABC):
    """方針の見積もりに共通の欄。図の形と設計書の形は、それぞれの型で表す。"""

    largest_sections: tuple[SectionSize, ...]
    materials_bytes: int
    planned_invocations: int

    @property
    @abstractmethod
    def shape(self) -> PolicyShape:
        """この型の設計書の形。型から決まり、欄としては持たない。"""

    @property
    def discovery_calls(self) -> int:
        return self.planned_invocations

    @property
    def has_verifier(self) -> bool:
        return False

    def policy_shape(self) -> PolicyShape | None:
        return self.shape

    def policy_notice_text(self) -> str | None:
        # 注はシャードを持つ群の欄で、方針は持たない
        return None

    def largest_section_entries(self) -> tuple[SectionSize, ...]:
        return self.largest_sections

    def _specific_record(self) -> dict[str, Any]:
        return {
            "largest_sections": [item.as_record() for item in self.largest_sections],
            "shape": self.shape,
            "materials_bytes": self.materials_bytes,
            "planned_invocations": self.planned_invocations,
        }


@dataclass(frozen=True)
class DiagramPolicyEstimate(PolicyEstimateBase):
    """図の形の方針の見積もり。出力と推論の分の見込みは持たない。"""

    @property
    def shape(self) -> PolicyShape:
        return PolicyShape.DIAGRAM

    def expected_output(self) -> OutputEstimate | None:
        # 出力と推論の分の見込みは設計書の形だけが持つ
        return None

    def input_cost(self, basis: claude.RunBudget) -> InputCostEstimate | None:
        cost = claude.input_cost_usd(basis.model, self.discovery_input_tokens)
        return InputCostEstimate(cost, basis.input_stops_run(self.discovery_input_tokens))


@dataclass(frozen=True)
class DesignPolicyEstimate(PolicyEstimateBase):
    """設計書の形の方針の見積もり。出力と推論の分を含めた見込みを必ず持つ。"""

    expected: OutputEstimate

    @property
    def shape(self) -> PolicyShape:
        return PolicyShape.DESIGN

    def expected_output(self) -> OutputEstimate | None:
        return self.expected

    def input_cost(self, basis: claude.RunBudget) -> InputCostEstimate | None:
        cost = claude.input_cost_usd(basis.model, self.discovery_input_tokens)
        return ExpectedInputCost(
            cost,
            basis.input_stops_run(self.discovery_input_tokens),
            self.expected,
            self.expected.total_usd >= basis.max_budget_usd,
        )


PolicyEstimate = DiagramPolicyEstimate | DesignPolicyEstimate


@dataclass(frozen=True)
class ShardedEstimate(EstimateCommon, ABC):
    """シャードごとに発見役を起動する群に共通の欄。"""

    policy_notice: str | None
    target_files: int
    shard_bytes: tuple[int, ...]
    # 前の回の指摘の節や MCP の注記を含めた、シャードごとの実際のプロンプトの大きさ。
    # 実行が発見役に渡すプロンプトと同じ組み立てで求める
    prompt_bytes: tuple[int, ...]

    @property
    def discovery_calls(self) -> int:
        return len(self.shard_bytes)

    @property
    def has_verifier(self) -> bool:
        return True

    def policy_shape(self) -> PolicyShape | None:
        # 設計書の形は方針だけが持つ
        return None

    def expected_output(self) -> OutputEstimate | None:
        # 出力と推論の分の見込みは設計書の形だけが持つ
        return None

    def policy_notice_text(self) -> str | None:
        return self.policy_notice

    def largest_section_entries(self) -> tuple[SectionSize, ...]:
        # シャードのプロンプトは節ごとの大きさを持たない
        return ()

    def _specific_record(self) -> dict[str, Any]:
        return {
            "policy_notice": self.policy_notice,
            "target_files": self.target_files,
            "shard_bytes": list(self.shard_bytes),
            "prompt_bytes": list(self.prompt_bytes),
        }

    def input_cost(self, basis: claude.RunBudget) -> InputCostEstimate | None:
        # 上限は起動ごとなので、シャード単位で比べる
        tokens = [claude.estimate_input_tokens(size) for size in self.prompt_bytes]
        costs = [claude.input_cost_usd(basis.model, count) for count in tokens]
        return ShardedInputCost(
            cost=claude.InputCost(
                sum(cost.usd for cost in costs),
                costs[0].price_per_million if costs else 0.0,
                any(cost.assumed_price for cost in costs),
            ),
            exceeds_budget=any(basis.input_stops_run(count) for count in tokens),
            shard_cost_usd=tuple(cost.usd for cost in costs),
        )


@dataclass(frozen=True)
class ImplementationEstimate(ShardedEstimate):
    """実装の群の見積もり。欄と入力の費用は ShardedEstimate が持つ。"""


@dataclass(frozen=True)
class SecurityEstimate(ShardedEstimate):
    """セキュリティの群の見積もり。欄と入力の費用は ShardedEstimate が持つ。"""


Estimate = PolicyEstimate | ImplementationEstimate | SecurityEstimate
