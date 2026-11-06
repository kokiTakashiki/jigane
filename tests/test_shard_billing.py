"""micro と security の上限を、実際の課金（書き込み、読み出し、出力）に合わせる試験。"""

from pathlib import Path

import pytest
from fakes import FakeInvoke, settings_for

from jigane.groups.definitions import GROUP_BY_ID, MACRO, MICRO, SECURITY, GroupId
from jigane.llm_runs import claude, pricing
from jigane.skills import input_budget

SONNET = "claude-sonnet-5-5"

# 実測の発見役 8 件: (input, cache_creation, cache_read, output, cost_usd)。
# 出所は配布前の範囲のレビューの runs.json（model は claude-sonnet-5-5）
MEASURED_DISCOVERIES = [
    (6, 4765, 356871, 4077, 0.0955291),
    (4, 2038, 237856, 2048, 0.0524256),
    (4, 2536, 239698, 2558, 0.0597018),
    (6, 3764, 379874, 2184, 0.0748954),
    (6, 3660, 353128, 2535, 0.0753148),
    (8, 2998, 477683, 1541, 0.0751863),
    (2, 0, 119273, 652, 0.0184513),
    (2, 0, 125551, 779, 0.0203491),
]
# 冷えた実測（284,438 バイトのプロンプトの最初の往復）。並びは上と同じ
COLD_MEASUREMENT = (2, 121694, 4631, 2390, 0.5111431)
COLD_PROMPT_BYTES = 284438
# 冷えた完走の実測（228,581 バイト）。並びは上と同じ。最初の往復は書き込み 97,402、読み出し 4,631、
# 出力 570、入力 2 で、CLI は往復の合間に、ここまでの額を渡した上限と比べる
COLD_COMPLETED = (4, 98483, 106664, 1177, 0.4163764)
COLD_COMPLETED_FIRST_ROUND_TRIP = (2, 97402, 4631, 570)
COLD_COMPLETED_PROMPT_BYTES = 228581
# 起動前の判定が止め始める入力トークン数。T を百万トークンとして、
# 4.00 * T + 0.04 >= 0.5 - 0.10 * T - 0.04 を初めて満たす整数
STOP_BEGINS_AT_TOKENS = 102_440
COLD_INPUT_TOKENS = 126327


def budget_of(group_id: GroupId) -> claude.RunBudget:
    shape = GROUP_BY_ID[group_id].shape
    return claude.RunBudget(
        GROUP_BY_ID[group_id].model,
        shape.max_budget_usd,
        shape.reserve_margin,
        output_allowance_tokens=shape.output_allowance_tokens,
    )


@pytest.mark.parametrize(
    ("fresh", "written", "read", "output", "cost"),
    [*MEASURED_DISCOVERIES, COLD_MEASUREMENT, COLD_COMPLETED],
)
def test_price_table_reproduces_measured_cost(
    fresh: int, written: int, read: int, output: int, cost: float
) -> None:
    prices = pricing.MODEL_PRICES[SONNET]
    tier = prices.tiers[0]
    computed = (
        fresh * prices.input
        + written * tier.cache_write_1h
        + read * tier.cache_read
        + output * tier.output
    ) / 1_000_000
    assert computed == pytest.approx(cost, abs=1e-6)


def test_cold_measurement_matches_the_bytes_per_token() -> None:
    assert sum(COLD_MEASUREMENT[:3]) == COLD_INPUT_TOKENS
    assert pricing.billed_input_tokens(COLD_PROMPT_BYTES) == pytest.approx(
        COLD_INPUT_TOKENS, rel=0.01
    )


@pytest.mark.parametrize("prompt_bytes", [279084, 284381])
def test_largest_shards_are_stopped_before_launch(prompt_bytes: int) -> None:
    budget = budget_of(MICRO)
    tokens = budget.prompt_tokens(prompt_bytes)
    assert budget.input_stops_run(tokens)
    assert budget.launch_cost(tokens).usd >= 0.5


def test_expected_numbers_at_the_largest_measured_shard() -> None:
    budget = budget_of(MICRO)
    tokens = budget.prompt_tokens(284381)
    assert budget.launch_cost(tokens).usd == pytest.approx(0.546, abs=1e-3)


def test_roomy_shard_is_launched_with_a_cli_limit_of_the_remaining_budget() -> None:
    budget = budget_of(MICRO)
    tokens = budget.prompt_tokens(220_000)
    assert not budget.input_stops_run(tokens)
    cli = budget.cli_usd(tokens)
    assert cli is not None
    assert cli >= 0.4
    read = tokens * 0.10 / 1_000_000
    assert cli == pytest.approx(0.5 - read - 0.04)


def test_stop_begins_at_102440_tokens() -> None:
    for group in (MICRO, SECURITY):
        budget = budget_of(group)
        assert not budget.input_stops_run(STOP_BEGINS_AT_TOKENS - 1)
        assert budget.input_stops_run(STOP_BEGINS_AT_TOKENS)
        assert not budget.input_stops_run(budget.prompt_tokens(230_000))
        assert budget.input_stops_run(budget.prompt_tokens(231_000))


def test_cold_completed_shard_passes_and_its_first_round_trip_stays_under_the_cli_limit() -> None:
    budget = budget_of(MICRO)
    tokens = budget.prompt_tokens(COLD_COMPLETED_PROMPT_BYTES)
    assert not budget.input_stops_run(tokens)
    cli = budget.cli_usd(tokens)
    assert cli is not None
    assert cli == pytest.approx(0.4498, abs=1e-3)
    fresh, written, read, output = COLD_COMPLETED_FIRST_ROUND_TRIP
    prices = pricing.MODEL_PRICES[SONNET]
    tier = prices.tiers[0]
    first_round_trip = (
        fresh * prices.input
        + written * tier.cache_write_1h
        + read * tier.cache_read
        + output * tier.output
    ) / 1_000_000
    assert first_round_trip < cli


def test_cold_stopped_shard_is_stopped_before_launch() -> None:
    budget = budget_of(MICRO)
    assert budget.input_stops_run(budget.prompt_tokens(COLD_PROMPT_BYTES))


def test_policy_and_design_judgement_is_unchanged() -> None:
    diagram = budget_of(MACRO)
    assert not diagram.reserve_margin
    assert diagram.input_stops_run(100_000) == (diagram.launch_cost(100_000).usd >= 0.36)
    assert not diagram.input_stops_run(diagram.prompt_tokens(100_000))
    design = claude.RunBudget(SONNET, 0.5, reserve_margin=False)
    assert design.input_stops_run(250_000)
    assert not design.input_stops_run(249_999)


def test_both_groups_share_the_output_allowance() -> None:
    assert GROUP_BY_ID[MICRO].shape.output_allowance_tokens == 4_000
    assert GROUP_BY_ID[SECURITY].shape.output_allowance_tokens == 4_000
    assert GROUP_BY_ID[MICRO].shape.reserve_margin
    assert GROUP_BY_ID[SECURITY].shape.reserve_margin


def test_shape_without_output_allowance_keeps_the_old_numbers() -> None:
    budget = claude.RunBudget(SONNET, 0.5, reserve_margin=True)
    tokens = 30_000
    assert budget.cli_usd(tokens) == pytest.approx(0.5 - tokens * 2.00 / 1_000_000)
    assert budget.cli_usd(130_000) is None
    assert budget.input_stops_run(130_000)
    assert not budget.input_stops_run(100_000)
    unreserved = claude.RunBudget(SONNET, 0.5, reserve_margin=False)
    assert unreserved.input_stops_run(250_000)
    assert not unreserved.input_stops_run(249_999)


def test_oversized_prompt_stops_before_launch_with_an_accurate_message() -> None:
    settings = claude.RunSettings(budget_of(MICRO), None, FakeInvoke())
    prompt = "a" * 400_000
    stop = input_budget.check_input_budget(prompt, settings).stop
    assert stop is not None
    message = stop.message()
    assert "入力と出力の見込みで" in message
    assert "CLI に渡す上限" in message
    assert "次の一往復の分を引いた額" in message
    assert stop.estimated_usd >= 0.5


def test_invocation_passes_the_reduced_limit_to_the_cli() -> None:
    settings = claude.RunSettings(budget_of(MICRO), None, FakeInvoke())
    prompt = "a" * 220_000
    invocation = settings.invocation(prompt=prompt, json_schema={}, working_directory=Path())
    assert invocation.budget.limit_usd == 0.5
    assert invocation.budget.cli_usd == pytest.approx(0.4502, abs=1e-3)


def test_settings_for_default_budget_is_unchanged() -> None:
    settings = settings_for(FakeInvoke())
    assert settings.budget.output_allowance_tokens is None
    assert settings.budget.counts_output is False
