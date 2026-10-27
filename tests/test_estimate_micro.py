from typing import Any, assert_never

import pytest
from fakes import MacroFake, settings_for, with_budget

from jigane.commands import failures
from jigane.commands.subcommands.estimate import blocker_lines, input_cost_lines, notice_lines
from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude
from jigane.llm_runs.claude import INPUT_USD_PER_MILLION_TOKENS, RunBudget
from jigane.skills import input_budget
from jigane.skills.estimate_result import (
    DesignPolicyEstimate,
    DiagramPolicyEstimate,
    ExpectedInputCost,
    ImplementationEstimate,
    InputCostEstimate,
    SecurityEstimate,
)
from jigane.skills.input_budget import SectionSize
from jigane.skills.output_estimate import MultiplierSource, OutputEstimate, warning_message

MODEL = next(iter(INPUT_USD_PER_MILLION_TOKENS))


def micro_estimate(prompt_bytes: list[int]) -> ImplementationEstimate:
    return ImplementationEstimate(
        discovery_input_tokens=0,
        past_run=None,
        note="",
        policy_notice=None,
        target_files=0,
        shard_bytes=(),
        prompt_bytes=tuple(prompt_bytes),
    )


def micro_record(
    prompt_bytes: list[int], max_budget_usd: float, model: str = MODEL
) -> dict[str, Any]:
    return micro_estimate(prompt_bytes).input_cost_record(
        RunBudget(model, max_budget_usd, reserve_margin=False)
    )


def shard_cost(size: int) -> float:
    return claude.input_cost_usd(MODEL, claude.estimate_input_tokens(size)).usd


def test_micro_input_cost_is_the_sum_of_shard_prompts() -> None:
    record = micro_record([300, 600], 100.0)

    assert record["input_cost_usd"] == shard_cost(300) + shard_cost(600)
    assert record["input_price_usd_per_million_tokens"] == INPUT_USD_PER_MILLION_TOKENS[MODEL]
    assert record["input_price_assumed"] is False
    assert record["exceeds_budget"] is False


def test_micro_record_carries_the_input_cost_of_each_shard() -> None:
    record = micro_record([300, 600], 100.0)

    assert record["shard_input_cost_usd"] == [shard_cost(300), shard_cost(600)]


def test_micro_budget_is_compared_per_shard_not_by_the_sum() -> None:
    one_shard = shard_cost(3_000_000)
    # 合計は上限を超えるが、どのシャードも上限未満
    below = micro_record([3_000_000, 3_000_000], one_shard * 1.5)
    assert below["input_cost_usd"] > one_shard * 1.5
    assert below["exceeds_budget"] is False
    # 1 シャードだけが上限に達する
    assert micro_record([300, 3_000_000], one_shard)["exceeds_budget"] is True


def test_micro_input_cost_without_shards_is_zero() -> None:
    record = micro_record([], 0.5)

    assert record["input_cost_usd"] == 0
    assert record["input_price_usd_per_million_tokens"] == 0.0
    assert record["exceeds_budget"] is False


def test_micro_unpriced_model_is_marked_assumed() -> None:
    assert micro_record([300], 0.5, model="unpriced-model")["input_price_assumed"] is True


def test_cost_lines_omit_largest_sections_when_the_estimate_has_none() -> None:
    lines = input_cost_lines(
        micro_estimate([3_000_000]), RunBudget(MODEL, 0.5, reserve_margin=False)
    )

    assert lines[0].startswith("入力の費用の見積もり: ")
    assert "実行は止まります" in lines[-1]
    assert "大きい節" not in lines[-1]


def test_cost_lines_list_largest_sections_when_present() -> None:
    estimate = DiagramPolicyEstimate(
        discovery_input_tokens=claude.estimate_input_tokens(3_000_000),
        past_run=None,
        note="",
        largest_sections=(SectionSize("背景", 12), SectionSize("図", 3)),
        materials_bytes=3_000_000,
        planned_invocations=1,
    )

    lines = input_cost_lines(estimate, RunBudget(MODEL, 0.5, reserve_margin=False))

    assert lines[-1].endswith("大きい節: 背景（12 バイト）、図（3 バイト）")


def diagram_policy() -> DiagramPolicyEstimate:
    return DiagramPolicyEstimate(
        discovery_input_tokens=1000,
        past_run=None,
        note="",
        largest_sections=(),
        materials_bytes=0,
        planned_invocations=1,
    )


def design_policy(expected: OutputEstimate) -> DesignPolicyEstimate:
    return DesignPolicyEstimate(
        discovery_input_tokens=1000,
        past_run=None,
        note="",
        largest_sections=(),
        materials_bytes=0,
        planned_invocations=1,
        expected=expected,
    )


def test_policy_input_cost_record_nests_the_expectation_only_for_the_design_shape() -> None:
    input_usd = claude.input_cost_usd(MODEL, 1000).usd
    without = diagram_policy().input_cost_record(RunBudget(MODEL, 100.0, reserve_margin=False))
    assert list(without) == [
        "input_cost_usd",
        "input_price_usd_per_million_tokens",
        "input_price_assumed",
        "exceeds_budget",
    ]
    expected = OutputEstimate(input_usd, 6.0, MultiplierSource.DEFAULT)
    assert "expected" not in without
    with_expected = design_policy(expected).input_cost_record(
        RunBudget(MODEL, expected.total_usd, reserve_margin=False)
    )
    assert with_expected["exceeds_budget"] is False
    assert with_expected["expected"] == {
        **expected.as_record(),
        "exceeds_budget": True,
        "budget_warning": warning_message(expected, expected.total_usd),
    }
    below = design_policy(expected).input_cost_record(
        RunBudget(MODEL, expected.total_usd * 2, reserve_margin=False)
    )
    assert below["expected"]["exceeds_budget"] is False
    assert "budget_warning" not in below["expected"]
    assert set(with_expected) & {"exceeds_budget_expected", "expected_budget_warning"} == set()


def test_policy_input_cost_is_an_expected_input_cost_only_for_the_design_shape() -> None:
    basis = RunBudget(MODEL, 100.0, reserve_margin=False)
    plain = diagram_policy().input_cost(basis)
    assert type(plain) is InputCostEstimate
    expected = OutputEstimate(1.0, 6.0, MultiplierSource.DEFAULT)
    with_expected = design_policy(expected).input_cost(basis)
    assert isinstance(with_expected, ExpectedInputCost)
    assert with_expected.exceeds_budget_expected is False


def test_every_arm_of_the_policy_estimate_sum_yields_its_json_expectation() -> None:
    basis = RunBudget(MODEL, 100.0, reserve_margin=False)
    expected = OutputEstimate(1.0, 6.0, MultiplierSource.DEFAULT)
    for estimate in (diagram_policy(), design_policy(expected)):
        match estimate:
            case DiagramPolicyEstimate():
                assert estimate.to_record()["shape"] is PolicyShape.DIAGRAM
                assert "expected" not in estimate.input_cost_record(basis)
            case DesignPolicyEstimate():
                assert estimate.to_record()["shape"] is PolicyShape.DESIGN
                assert estimate.input_cost_record(basis)["expected"].keys() == {
                    "total_usd",
                    "output_multiplier",
                    "output_multiplier_source",
                    "exceeds_budget",
                }
            case _:
                assert_never(estimate)


def security_estimate(prompt_bytes: list[int]) -> SecurityEstimate:
    return SecurityEstimate(
        discovery_input_tokens=0,
        past_run=None,
        note="",
        policy_notice=None,
        target_files=0,
        shard_bytes=(),
        prompt_bytes=tuple(prompt_bytes),
    )


def test_security_input_cost_is_the_sum_of_shard_prompts() -> None:
    record = security_estimate([300, 600]).input_cost_record(
        RunBudget(MODEL, 100.0, reserve_margin=False)
    )

    assert record["input_cost_usd"] == shard_cost(300) + shard_cost(600)
    assert record["shard_input_cost_usd"] == [shard_cost(300), shard_cost(600)]
    assert record["exceeds_budget"] is False
    assert "budget_stop_warning" not in record


def test_cost_lines_warn_when_the_expected_total_reaches_the_limit_but_input_does_not() -> None:
    estimate = design_policy(
        OutputEstimate(claude.input_cost_usd(MODEL, 1000).usd, 6.0, MultiplierSource.DEFAULT)
    )
    limit = estimate.expected_total_usd()
    assert limit is not None

    lines = input_cost_lines(estimate, RunBudget(MODEL, limit, reserve_margin=False))

    assert len(lines) == 2
    assert lines[1] == warning_message(estimate.expected, limit)
    # 文面の要点を縛る。同じ関数との一致だけでは、文面が壊れても通るため
    assert "目安に達します" in lines[1]
    assert f"見込み {estimate.expected.total_usd:.4f} USD" in lines[1]
    assert f"目安 {limit:g} USD" in lines[1]


def test_notice_lines_carry_the_policy_notice_only_when_there_is_one() -> None:
    def sharded(notice: str | None) -> SecurityEstimate:
        return SecurityEstimate(
            discovery_input_tokens=0,
            past_run=None,
            note="",
            policy_notice=notice,
            target_files=0,
            shard_bytes=(),
            prompt_bytes=(),
        )

    assert notice_lines(sharded("方針は未レビュー")) == ["注: 方針のレビュー: 方針は未レビュー"]
    assert notice_lines(sharded(None)) == []


def test_blocker_lines_give_the_reason_then_the_action() -> None:
    blocker = failures.Blocker(
        failures.BlockerKind.SECURITY_CONFIG,
        "題",
        "MCP が無い",
        "設定を足す",
    )

    assert blocker_lines(()) == []
    assert blocker_lines((blocker,)) == ["実行できません: MCP が無い", "次の操作: 設定を足す"]


SIZE = 300_000


def micro_record_with(basis: RunBudget) -> dict[str, Any]:
    return micro_estimate([SIZE]).input_cost_record(basis)


@pytest.mark.parametrize(
    ("cost_over_limit", "stops"),
    [(0.4, False), (0.6, True), (0.9, True), (1.1, True)],
)
def test_reserving_group_estimate_and_pre_run_check_stop_at_the_same_inputs(
    cost_over_limit: float, *, stops: bool
) -> None:
    limit = shard_cost(SIZE) / cost_over_limit
    settings = with_budget(settings_for(MacroFake([]), max_budget_usd=limit), model=MODEL)

    record = micro_record_with(RunBudget(MODEL, limit, reserve_margin=True))
    check = input_budget.check_input_budget("a" * SIZE, settings)

    assert record["exceeds_budget"] is stops
    assert (check.stop is not None) is stops


def test_non_reserving_group_stops_only_at_the_limit_itself() -> None:
    limit = shard_cost(SIZE) / 0.6

    assert (
        micro_record_with(RunBudget(MODEL, limit, reserve_margin=False))["exceeds_budget"] is False
    )
    assert (
        micro_record_with(RunBudget(MODEL, limit * 0.5, reserve_margin=False))["exceeds_budget"]
        is True
    )


def test_reserving_estimate_line_names_the_margin_limit() -> None:
    limit = shard_cost(SIZE) / 0.6
    lines = input_cost_lines(micro_estimate([SIZE]), RunBudget(MODEL, limit, reserve_margin=True))

    assert any("次の一往復の分を引いた額" in line and "実行は止まります" in line for line in lines)
