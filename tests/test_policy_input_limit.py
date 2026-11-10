"""方針の起動（図の起動と原則の起動）の入力の上限を、実際の課金に合わせた判定の試験。"""

import json
import math
from pathlib import Path

import pytest
from fakes import FakeInvoke, MacroFake, settings_for, with_budget

from jigane.commands import failures, main
from jigane.commands.exit_codes import EXIT_OK
from jigane.groups.definitions import MACRO, MICRO, SECURITY, GroupId
from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude, pricing
from jigane.llm_runs.defaults import resolve_run_defaults
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import discovery_errors, input_budget, macro, macro_principles_run
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import (
    DesignPolicyEstimate,
    ImplementationEstimate,
    SecurityEstimate,
    ShardedInputCost,
)
from jigane.skills.output_estimate import MultiplierSource, OutputEstimate
from jigane.skills.policy_principles import principle_prompts
from jigane.skills.sharded import estimate_sharded

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def diagram_defaults() -> claude.RunBudget:
    return resolve_run_defaults(MACRO, PolicyShape.DIAGRAM, None).budget


def stops(budget: claude.RunBudget, prompt_bytes: int) -> bool:
    settings = claude.RunSettings(budget, None, FakeInvoke())
    return input_budget.check_input_budget("a" * prompt_bytes, settings).stop is not None


def bytes_for_tokens(tokens: int) -> int:
    return math.floor(tokens * pricing.BILLED_BYTES_PER_TOKEN)


@pytest.mark.parametrize("prompt_bytes", [424_351, 360_645, 502_737])
def test_measured_oversized_principle_prompts_are_skipped(prompt_bytes: int) -> None:
    principle = diagram_defaults().for_principle("P1")
    assert principle.model == HAIKU
    assert principle.max_budget_usd == 0.04
    assert stops(principle, prompt_bytes)


@pytest.mark.parametrize("tokens", [22_637, 24_454])
def test_measured_largest_runnable_principle_inputs_run(tokens: int) -> None:
    principle = diagram_defaults().for_principle("P1")
    prompt_bytes = bytes_for_tokens(tokens)
    assert principle.prompt_tokens(prompt_bytes) == tokens
    assert not stops(principle, prompt_bytes)


def test_measured_largest_diagram_input_runs() -> None:
    budget = diagram_defaults()
    prompt_bytes = bytes_for_tokens(17_073)
    assert budget.prompt_tokens(prompt_bytes) == 17_073
    assert not stops(budget, prompt_bytes)


def test_haiku_boundary_is_100000_tokens() -> None:
    principle = diagram_defaults().for_principle("P2")
    assert stops(principle, 225_002)
    assert principle.prompt_tokens(225_000) == 100_000
    assert not stops(principle, 225_000)
    assert stops(principle, 225_001)


def test_sonnet_boundary_is_82500_tokens() -> None:
    budget = diagram_defaults()
    assert budget.model == SONNET
    assert stops(budget, 185_625)
    assert budget.prompt_tokens(185_622) == 82_499
    assert not stops(budget, 185_622)


def test_haiku_price_tier_follows_the_prompt_length() -> None:
    budget = claude.RunBudget(HAIKU, 10.0, reserve_margin=False, output_allowance_tokens=12_000)
    assert budget.launch_cost(100_000).usd == pytest.approx(0.026)
    assert budget.launch_cost(100_001).usd == pytest.approx(0.130001)


def test_unlisted_model_gets_the_highest_price_and_is_marked() -> None:
    cost = claude.RunBudget(
        "unlisted", 1.0, reserve_margin=False, output_allowance_tokens=1000
    ).launch_cost(1000)
    assert cost.assumed_price
    assert cost.usd == pytest.approx((1000 * 20.0 + 1000 * 50.0) / 1_000_000)


def test_implementation_and_security_judgement_follows_the_billing() -> None:
    for group in (MICRO, SECURITY):
        budget = resolve_run_defaults(group, None, None).budget
        assert budget.output_allowance_tokens == 4_000
        assert budget.prompt_tokens(900) == 400
        # 1,000,000 トークンの書き込み 4.00 USD に、4,000 トークンの出力 0.04 USD を足す
        assert budget.launch_cost(1_000_000).usd == pytest.approx(4.04)
        assert pricing.input_cost_usd(HAIKU, 1_000_000).usd == 0.1
        # 4.00 * T + 0.04 >= 0.5 - 0.10 * T - 0.04（T は百万トークン）を満たす最小は 102,440
        assert budget.input_stops_run(102_440)
        assert not budget.input_stops_run(102_439)


def test_design_shape_counts_the_output_allowance() -> None:
    budget = resolve_run_defaults(MACRO, PolicyShape.DESIGN, None).budget
    assert budget.output_allowance_tokens == 20_000
    assert budget.prompt_tokens(900) == 400
    assert budget.launch_cost(400) == pricing.billed_launch_cost(SONNET, 400, 20_000)
    # 完走した設計書の形の起動 33 本の最大の入力 62,493 トークンでも、見送りにならない
    assert not budget.input_stops_run(62_493)


def test_model_and_limit_flags_keep_the_output_allowance() -> None:
    budget = resolve_run_defaults(
        MACRO, PolicyShape.DIAGRAM, None, model="claude-opus-5-5", max_budget_usd=1.0
    ).budget
    assert budget.output_allowance_tokens == 3000
    assert budget.model == "claude-opus-5-5"
    assert budget.for_principle("P3").output_allowance_tokens == 30_000
    assert budget.for_principle("P3").max_budget_usd == 1.0
    assert (
        with_budget(settings_for(FakeInvoke()), model=HAIKU).budget.output_allowance_tokens is None
    )
    assert (
        with_budget(
            claude.RunSettings(budget, None, FakeInvoke()), model=HAIKU
        ).budget.output_allowance_tokens
        == 3000
    )


def test_stop_message_names_the_output_estimate_only_for_the_diagram_shape() -> None:
    diagram = input_budget.check_input_budget(
        "a" * 600_000, claude.RunSettings(diagram_defaults(), None, FakeInvoke())
    ).stop
    assert diagram is not None
    assert "入力と出力の見込みで" in diagram.message()
    legacy = input_budget.check_input_budget(
        "a" * 600_000,
        claude.RunSettings(claude.RunBudget(SONNET, 0.5, reserve_margin=True), None, FakeInvoke()),
    ).stop
    assert legacy is not None
    assert "入力だけで" in legacy.message()
    assert "出力の見込み" not in legacy.message()


@pytest.mark.usefixtures("all_principle_shapes")
def test_estimate_amount_equals_the_sum_of_the_amounts_used_to_decide(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    assembly = AssemblyInput.load(review)
    budget = diagram_defaults()
    expected = budget.launch_cost(
        budget.prompt_tokens(len(macro.build_diagram_prompt(assembly).encode("utf-8")))
    ).usd + sum(
        budget.for_principle(item.principle.number)
        .launch_cost(
            budget.for_principle(item.principle.number).prompt_tokens(
                len(item.prompt.encode("utf-8"))
            )
        )
        .usd
        for item in principle_prompts(assembly)
    )
    assert main(["estimate", "shop:p", "macro", "--json"]) == EXIT_OK
    record = json.loads(capsys.readouterr().out)
    assert record["input_cost_usd"] == pytest.approx(expected)
    assert main(["estimate", "shop:p", "macro"]) == EXIT_OK
    assert f"入力と出力の見込みの費用の見積もり: {expected:.4f} USD" in capsys.readouterr().out


def test_all_launches_skipped_spends_nothing_and_points_at_the_limit_flag(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([])
    settings = claude.RunSettings(
        claude.RunBudget(SONNET, 0.0001, reserve_margin=False, output_allowance_tokens=3000),
        None,
        fake,
    )
    with pytest.raises(discovery_errors.InputBudgetError) as raised:
        macro_principles_run.run_policy(AssemblyInput.load(review), settings)
    assert fake.calls == []
    assert "--max-budget-usd-per-call" in failures.describe(raised.value).action


def test_unlisted_model_principle_launch_is_skipped_even_for_a_tiny_prompt() -> None:
    defaults = resolve_run_defaults(MACRO, PolicyShape.DIAGRAM, None, model="unlisted")
    principle = defaults.budget.for_principle("P1")
    assert principle.model == "unlisted"
    # 出力の見込みだけで 30,000 × 50 USD / 100 万 = 1.5 USD となり、上限 0.04 USD を超える
    assert stops(principle, 1)


def test_unlisted_model_diagram_launch_switches_at_10500_tokens() -> None:
    budget = resolve_run_defaults(MACRO, PolicyShape.DIAGRAM, None, model="unlisted").budget
    below = bytes_for_tokens(10_499)
    at_limit = bytes_for_tokens(10_500)
    assert (below, at_limit) == (23_622, 23_625)
    assert not stops(budget, below)
    assert stops(budget, at_limit)


def sharded_estimate(
    cls: type[ImplementationEstimate] | type[SecurityEstimate], prompt_bytes: tuple[int, ...]
) -> ImplementationEstimate | SecurityEstimate:
    return cls(
        discovery_input_tokens=0,
        past_run=None,
        note="",
        policy_notice=None,
        target_files=0,
        shard_bytes=(),
        prompt_bytes=prompt_bytes,
    )


@pytest.mark.parametrize("group", [MICRO, SECURITY])
def test_sharded_estimate_amount_equals_the_launch_cost_used_to_decide(group: GroupId) -> None:
    budget = resolve_run_defaults(group, None, None).budget
    cls = ImplementationEstimate if group is MICRO else SecurityEstimate
    sizes = (90_000, 450_000)
    cost = sharded_estimate(cls, sizes).input_cost(budget)
    assert cost is not None
    decided = [
        input_budget.check_input_budget("a" * size, claude.RunSettings(budget, None, FakeInvoke()))
        for size in sizes
    ]
    launch_costs = [budget.launch_cost(budget.prompt_tokens(size)).usd for size in sizes]
    assert launch_costs[0] == pytest.approx(
        pricing.billed_launch_cost(budget.model, pricing.billed_input_tokens(sizes[0]), 4_000).usd
    )
    assert cost.cost.usd == pytest.approx(sum(launch_costs))
    assert isinstance(cost, ShardedInputCost)
    assert cost.shard_cost_usd == pytest.approx(tuple(launch_costs))
    assert cost.exceeds_budget is any(check.stop is not None for check in decided)
    assert cost.exceeds_budget


def test_sharded_discovery_tokens_use_the_billed_formula_without_a_budget(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    assembly = AssemblyInput.load(review)
    for budget in (None, resolve_run_defaults(MICRO, None, None).budget):
        estimate = estimate_sharded(ImplementationEstimate, assembly, MICRO, "", budget=budget)
        assert estimate.discovery_input_tokens == pricing.billed_input_tokens(
            sum(estimate.shard_bytes)
        )


def design_budget() -> claude.RunBudget:
    return resolve_run_defaults(MACRO, PolicyShape.DESIGN, None).budget


def test_design_estimate_amount_equals_the_launch_cost_used_to_decide() -> None:
    budget = design_budget()
    prompt_bytes = 140_625
    tokens = budget.prompt_tokens(prompt_bytes)
    estimate = DesignPolicyEstimate(
        discovery_input_tokens=tokens,
        past_run=None,
        note="",
        largest_sections=(),
        materials_bytes=prompt_bytes,
        planned_invocations=1,
        expected=OutputEstimate(0.1, 2.0, MultiplierSource.DEFAULT),
    )
    cost = estimate.input_cost(budget)
    assert cost is not None
    checked = input_budget.check_input_budget(
        "a" * prompt_bytes, claude.RunSettings(budget, None, FakeInvoke())
    )
    assert checked.stop is None
    assert cost.cost.usd == pytest.approx(budget.launch_cost(tokens).usd)
    assert cost.cost.usd == pytest.approx(pricing.billed_launch_cost(SONNET, tokens, 20_000).usd)
    # 出力の見込み 20,000 トークンの分が、入力だけの費用に上乗せされる
    assert cost.cost.usd > pricing.input_cost_usd(SONNET, tokens).usd


def test_design_shape_check_counts_the_output_allowance() -> None:
    budget = design_budget()
    # 入力だけの費用が 0.5 USD に届かない大きさでも、出力の見込みを足すと見送る
    tokens = 100_000
    assert pricing.input_cost_usd(SONNET, tokens).usd < budget.max_budget_usd
    assert budget.launch_cost(tokens).usd >= budget.max_budget_usd
    assert stops(budget, bytes_for_tokens(tokens))
