"""4 シャードのプロンプトが上限で止まる差分だけ、シャードを 8 個まで増やす試験。"""

from pathlib import Path

import pytest
from fakes import MicroFake, clear_policy, settings_for

from jigane.commands import consent
from jigane.groups.definitions import MICRO
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import discovery_errors, estimator, micro, sharded, shards
from jigane.skills.assembly import AssemblyInput
from jigane.skills.group_expectation import GroupExpectation, VerifierMultiplierSource
from jigane.skills.mcp import NO_MCP

FILE_BYTES = 50_000


def make_project(root: Path, file_count: int) -> Path:
    (root / "src").mkdir(parents=True)
    for number in range(file_count):
        body = f"# f{number}\n" + "x = 1\n" * (FILE_BYTES // 6)
        (root / "src" / f"f{number}.py").write_text(body, encoding="utf-8")
    return root


@pytest.fixture
def seven_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return make_project(tmp_path / "shop", 7)


@pytest.fixture
def nine_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return make_project(tmp_path / "shop", 9)


def budget_of(limit_usd: float) -> claude.RunBudget:
    return claude.RunBudget("m", limit_usd, reserve_margin=True)


def max_prompt_bytes(assembly: AssemblyInput, limit: int) -> int:
    plan = shards.build_plan(assembly.review, MICRO, limit)
    concerns = [sharded.shard_concerns(MICRO, assembly.review, shard) for shard in plan.shards]
    prompts = sharded.build_shard_prompts(MICRO, assembly, plan.shards, concerns, NO_MCP)
    return max(len(prompt.encode("utf-8")) for prompt in prompts)


def limit_passing(prompt_bytes: int) -> float:
    """そのプロンプトが止まらない最小の上限（USD）。"""
    limit = 0.01
    while budget_of(limit).input_stops_run(budget_of(limit).prompt_tokens(prompt_bytes)):
        limit += 0.005
    return round(limit, 3)


def grown_limit(assembly: AssemblyInput, count: int) -> float:
    """count 個に分けた最大のプロンプトは通り、count - 1 個では止まる上限。"""
    limit = limit_passing(max_prompt_bytes(assembly, count))
    wider = max_prompt_bytes(assembly, count - 1)
    assert budget_of(limit).input_stops_run(budget_of(limit).prompt_tokens(wider))
    return limit


def load(project: Path) -> tuple[Review, AssemblyInput]:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    return review, AssemblyInput.load(review)


def test_small_diff_keeps_the_default_count_and_assignment(tmp_path: Path) -> None:
    root = make_project(tmp_path / "small", 2)
    _, assembly = load(root)
    default = shards.build_plan(assembly.review, MICRO)

    planned = sharded.plan_shards(assembly, MICRO, NO_MCP, budget_of(0.5))

    assert [s.paths for s in planned.plan.shards] == [s.paths for s in default.shards]
    assert not planned.grown_unfit


def test_four_shards_that_fit_are_not_grown(seven_files: Path) -> None:
    _, assembly = load(seven_files)

    planned = sharded.plan_shards(assembly, MICRO, NO_MCP, budget_of(5.0))

    assert len(planned.plan.shards) == shards.MAX_SHARDS


def test_smallest_count_that_stops_nothing_is_chosen(seven_files: Path) -> None:
    _, assembly = load(seven_files)
    budget = budget_of(grown_limit(assembly, 7))

    planned = sharded.plan_shards(assembly, MICRO, NO_MCP, budget)

    assert len(planned.plan.shards) == 7
    assert not planned.grown_unfit
    assert all(
        not budget.input_stops_run(budget.prompt_tokens(len(prompt.encode("utf-8"))))
        for prompt in planned.prompts
    )


def test_run_estimate_and_consent_use_the_same_grown_count(seven_files: Path) -> None:
    _, assembly = load(seven_files)
    limit = grown_limit(assembly, 7)
    budget = budget_of(limit)
    fake = MicroFake({number: [] for number in range(1, 8)})

    estimate = micro.estimate_micro(assembly, MICRO, budget)
    assert estimator.estimate_of(assembly, MICRO, budget=budget).discovery_calls == 7
    settings = settings_for(fake, max_budget_usd=limit)
    plan = consent.launch_plan(
        estimator.estimate_of(assembly, MICRO, budget=settings.budget),
        GroupExpectation(0.2, 2.5, VerifierMultiplierSource.DEFAULT),
        MICRO,
        settings,
    )
    micro.run_micro(assembly, settings_for(fake, max_budget_usd=limit))

    assert estimate.discovery_calls == 7
    assert len(estimate.prompt_bytes) == 7
    assert plan.minimum_calls == 7
    assert sorted(fake.shard_prompts) == list(range(1, 8))
    assert sorted(len(p.encode("utf-8")) for p in fake.shard_prompts.values()) == sorted(
        estimate.prompt_bytes
    )


def test_unfit_diff_stays_at_eight_and_is_declined_before_any_call(nine_files: Path) -> None:
    _, assembly = load(nine_files)
    fake = MicroFake({})
    settings = settings_for(fake, max_budget_usd=0.05)

    planned = sharded.plan_shards(assembly, MICRO, NO_MCP, settings.budget)
    with pytest.raises(discovery_errors.InputBudgetError) as raised:
        micro.run_micro(assembly, settings)

    assert len(planned.plan.shards) == shards.MAX_GROWN_SHARDS
    assert planned.grown_unfit
    assert "8 個まで増やしても" in str(raised.value)
    assert "--max-budget-usd-per-call" in str(raised.value)
    assert fake.calls == []


def test_a_single_large_file_is_declined_with_the_same_guide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _, assembly = load(make_project(tmp_path / "shop", 1))
    fake = MicroFake({})

    with pytest.raises(discovery_errors.InputBudgetError):
        micro.run_micro(assembly, settings_for(fake, max_budget_usd=0.01))

    assert fake.calls == []


def test_partial_stop_after_growing_is_declined_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    project = make_project(tmp_path / "shop", 12)
    (project / "src" / "big.py").write_text("x = 1\n" * 60_000, encoding="utf-8")
    _, assembly = load(project)
    fake = MicroFake({})
    # 小さいシャードは通り、大きい 1 ファイルのシャードだけが止まる上限
    prompts = sharded.plan_shards(assembly, MICRO, NO_MCP, None).prompts
    small = limit_passing(min(len(p.encode("utf-8")) for p in prompts))
    settings = settings_for(fake, max_budget_usd=small)

    planned = sharded.plan_shards(assembly, MICRO, NO_MCP, settings.budget)
    with pytest.raises(discovery_errors.InputBudgetError) as raised:
        micro.run_micro(assembly, settings)

    assert planned.grown_unfit
    assert len(planned.plan.shards) == shards.MAX_GROWN_SHARDS
    assert "8 個まで増やしても" in str(raised.value)
    assert fake.calls == []


def test_partial_stop_without_growing_still_runs_the_fitting_shards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    project = make_project(tmp_path / "shop", 1)
    (project / "src" / "big.py").write_text("x = 1\n" * 60_000, encoding="utf-8")
    _, assembly = load(project)
    fake = MicroFake({})
    prompts = sharded.plan_shards(assembly, MICRO, NO_MCP, None).prompts
    sizes = sorted(len(p.encode("utf-8")) for p in prompts)
    assert len(sizes) < shards.MAX_SHARDS
    settings = settings_for(fake, max_budget_usd=limit_passing(sizes[0]))

    planned = sharded.plan_shards(assembly, MICRO, NO_MCP, settings.budget)

    assert any(
        settings.budget.input_stops_run(settings.budget.prompt_tokens(len(p.encode("utf-8"))))
        for p in planned.prompts
    )
    assert not planned.grown_unfit
