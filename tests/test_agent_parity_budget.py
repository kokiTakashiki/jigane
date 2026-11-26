"""既定単価と実単価による起動前検査。上流 CLI は呼ばない。"""

from dataclasses import replace
from pathlib import Path

import pytest
from fakes import FakeInvoke

from jigane.groups.definitions import MACRO
from jigane.groups.policy_runs import PolicyShape
from jigane.llm_runs import claude, codex
from jigane.llm_runs.defaults import preflight_budget, resolve_run_defaults
from jigane.skills import input_budget


@pytest.mark.parametrize(("principle", "limit"), [("P3", 0.175), ("P4", 0.325)])
@pytest.mark.parametrize("compact", [True, False])
def test_codex_principle_keeps_the_claude_cutoff(
    principle: str, limit: float, *, compact: bool
) -> None:
    shape = PolicyShape.DIAGRAM
    defaults = resolve_run_defaults(MACRO, shape, None).budget
    fake = FakeInvoke()
    native = claude.RunSettings(defaults.for_principle(principle), None, fake)
    login_budget = replace(codex.review_budget(defaults), compact_materials=compact)
    login = claude.RunSettings(login_budget, None, fake, has_spending_limit=False)
    budget = preflight_budget(login, shape=shape, principle=principle)
    assert budget.effective_limit(0, budget.launch_cost(0)) == pytest.approx(limit)
    # 75,000 字 × 3 バイト ÷ 2.25 で、Haiku の下の単価の上限 100,000 トークン。
    for prompt, stopped in (("字" * 75_000, False), ("字" * 75_000 + "a", True)):
        claude_stop = input_budget.check_input_budget(prompt, native).stop
        codex_stop = input_budget.check_input_budget(
            prompt, login, shape=shape, principle=principle
        ).stop
        assert (claude_stop is not None) == stopped
        assert codex_stop == claude_stop


@pytest.mark.parametrize(("limit", "stopped"), [(0.15, True), (0.150000001, False)])
def test_explicit_cap_compares_the_actual_price(limit: float, *, stopped: bool) -> None:
    shape = PolicyShape.DIAGRAM
    defaults = resolve_run_defaults(MACRO, shape, None, max_budget_usd=limit).budget
    settings = claude.RunSettings(
        codex.review_budget(defaults), None, FakeInvoke(), has_spending_limit=False
    )
    check = input_budget.check_input_budget("", settings, shape=shape, principle="P3")
    assert (check.stop is not None) == stopped
    if check.stop is not None:
        assert check.stop.estimated_usd == check.stop.limit_usd == limit


@pytest.mark.parametrize(("cap", "effective"), [(None, 0.325), (0.31, 0.31)])
def test_claude_passes_the_effective_cap_to_its_cli(
    tmp_path: Path, cap: float | None, effective: float
) -> None:
    defaults = resolve_run_defaults(
        MACRO, PolicyShape.DIAGRAM, None, "claude-sonnet-5-5", cap
    ).budget
    settings = claude.RunSettings(defaults.for_principle("P3"), None, FakeInvoke())
    call = settings.invocation(prompt="", json_schema={}, working_directory=tmp_path)
    assert call.budget.limit_usd == call.budget.cli_usd == effective
    argv = claude.build_command(call)
    assert argv[argv.index("--max-budget-usd") + 1] == str(effective)


def test_sol_design_budget_keeps_the_reference_stop() -> None:
    shape = PolicyShape.DESIGN
    defaults = resolve_run_defaults(MACRO, shape, None).budget
    native = claude.RunSettings(defaults, None, FakeInvoke())
    settings = claude.RunSettings(
        codex.review_budget(defaults), None, FakeInvoke(), has_spending_limit=False
    )
    budget = preflight_budget(settings, shape=shape)
    tokens = 87_500
    cost = budget.launch_cost(tokens)
    assert budget.model == "gpt-6.2-sol"
    assert cost.usd == 0.6375
    assert defaults.launch_cost(tokens).usd == 0.55
    assert budget.effective_limit(tokens, cost) == pytest.approx(0.6375)
    # 80,821 トークンは、以前の上限に達していた固定資料の起動前検査と同じ大きさ。
    # 「字」は 3 バイトで、概算の入力 1 トークンは 2.25 バイト。
    old_stop_prompt = "字" * 60_615 + "a"
    for run in (native, settings):
        assert input_budget.check_input_budget(old_stop_prompt, run, shape=shape).stop is None
        stopped = input_budget.check_input_budget("字" * 65_625, run, shape=shape).stop
        assert stopped is not None
        assert stopped.tokens == tokens
        assert stopped.estimated_usd == stopped.limit_usd == 0.55
