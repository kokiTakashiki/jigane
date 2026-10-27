import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    RECORD,
    FakeInvoke,
    MacroFake,
    MicroFake,
    as_run,
    clear_policy,
    d_finding,
    discovered,
    settings_for,
    with_budget,
)
from shard_prompt_support import shard_prompt

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.commands.subcommands.run import failure_reason_lines, group_defaults
from jigane.groups.definitions import MACRO, MICRO
from jigane.llm_runs import claude
from jigane.llm_runs.cost import Measured
from jigane.llm_runs.launch_failure import Disposition
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import adversarial, input_budget, macro, micro, pipeline, shards
from jigane.skills.assembly import AssemblyInput

# 単価の表に無いモデル "m" は表の最高の単価（10 USD / 100 万トークン）で見積もる
PRICE_PER_TOKEN = 10 / 1_000_000


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "big.py").write_text("x = 1\n" * 5000, encoding="utf-8")
    (root / "src" / "small.py").write_text("y = 2\n" * 1000, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def shard_costs(review: Review) -> list[float]:
    shard_list = shards.assign_shards(shards.build_units(review), shards.FullTextShard)
    total = len(shard_list)
    return [
        claude.estimate_input_tokens(
            len(
                shard_prompt(MICRO, AssemblyInput.load(review), shard, i + 1, total).encode(
                    "utf-8"
                )
            )
        )
        * PRICE_PER_TOKEN
        for i, shard in enumerate(shard_list)
    ]


def test_cli_budget_subtracts_one_more_round_of_input() -> None:
    tokens = 10_000
    expected = 0.5 - tokens * PRICE_PER_TOKEN
    assert claude.cli_budget_usd("m", tokens, 0.5) == pytest.approx(expected)
    # 余裕を引いた値が上限の半分を割る入力は None（走らせない）
    assert claude.cli_budget_usd("m", 30_000, 0.5) is None


def test_invocation_passes_reduced_budget_to_cli() -> None:
    settings = settings_for(FakeInvoke(), max_budget_usd=0.5)
    prompt = "a" * 30_000
    tokens = claude.estimate_input_tokens(len(prompt.encode("utf-8")))
    invocation = settings.invocation(prompt=prompt, json_schema={}, working_directory=Path())
    assert invocation.budget.cli_usd == pytest.approx(0.5 - tokens * PRICE_PER_TOKEN)
    command = claude.build_command(invocation)
    assert command[command.index("--max-budget-usd") + 1] == str(invocation.budget.cli_usd)
    unreduced = with_budget(settings, reserve_margin=False).invocation(
        prompt=prompt, json_schema={}, working_directory=Path()
    )
    assert unreduced.budget.cli_usd == 0.5


def test_shard_stopped_before_calling_when_estimate_reaches_limit(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    big, small = shard_costs(review)
    assert big > small
    fake = MicroFake({1: [], 2: []})
    # 大きいシャードだけが、引いた値が上限の半分を割る
    result = micro.run_micro(
        AssemblyInput.load(review), settings_for(fake, max_budget_usd=big + small)
    )
    assert len(fake.calls) == 1
    assert result.failed_shards == 1
    [stop] = result.run_state.to_json()["budget_stops"]
    assert (stop["role"], stop["index"]) == ("discovery", 0)
    assert stop["max_budget_usd"] == big + small
    assert stop["largest_sections"]


def test_all_shards_stopped_raises_with_reason(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    fake = MicroFake({1: [], 2: []})
    with pytest.raises(pipeline.InputBudgetError) as raised:
        micro.run_micro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.001))
    assert fake.calls == []
    message = str(raised.value)
    assert "発見役（シャード 0）" in message
    assert "トークン" in message
    assert "上限 0.001 USD" in message
    assert "大きい節: " in message


def test_verification_roles_are_stopped_and_recorded() -> None:
    fake = FakeInvoke()
    candidate = {**discovered("M2"), "id": "x"}
    outcome = adversarial.verify(
        [candidate],
        group=MACRO,
        changes="",
        working_directory=Path(),
        settings=settings_for(fake, max_budget_usd=0.001),
    )
    assert fake.calls == []
    assert outcome.failed_splits == 1
    assert outcome.unverified_candidates == 1
    [stop] = outcome.budget_stops
    assert stop["role"] in {"rebuttal", "alternative"}
    assert stop["estimated_input_tokens"] > 0
    # 起動していないので、起動の失敗としては残さない
    assert outcome.failures == ()


def test_rerun_stopped_by_input_budget_keeps_the_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = False
    real_check = input_budget.check_input_budget

    def invoke(_invocation: claude.Invocation) -> claude.InvocationResult:
        nonlocal failed
        failed = True
        raise claude.ClaudeInvocationError("529 overloaded", RECORD, claude.FailureKind.OVERLOADED)

    def check(
        text: str,
        settings: claude.RunSettings,
        *,
        role: str | None = None,
        index: int | None = None,
    ) -> input_budget.BudgetCheck:
        # 一回目の起動は通し、走らせ直しの検査だけを上限に掛ける
        tight = with_budget(settings, max_budget_usd=0.001) if failed else settings
        return real_check(text, tight, role=role, index=index)

    monkeypatch.setattr(input_budget, "check_input_budget", check)
    outcome = adversarial.verify(
        [{**discovered("M2"), "id": "x"}],
        group=MACRO,
        changes="",
        working_directory=Path(),
        settings=settings_for(invoke),
    )
    [failure] = outcome.failures
    assert failure.disposition is Disposition.RERUN_STOPPED
    assert failure.attempt == 1
    [stop] = outcome.budget_stops
    assert stop["estimated_input_tokens"] > 0
    assert outcome.failed_splits == 1
    assert failure_reason_lines(
        as_run({"launch_failures": [failure.as_record()]}).launch_failures or ()
    ) == [f"  検証の分割 0・{failure.role}（overloaded、1 回目）: 529 overloaded"]


class OverBudgetFake(MicroFake):
    def __call__(self, invocation: claude.Invocation) -> claude.InvocationResult:
        result = super().__call__(invocation)
        return replace(result, record=replace(result.record, cost=Measured(0.9)))


def over_budget_run(project: Path) -> tuple[Review, OverBudgetFake]:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    return review, OverBudgetFake({1: [d_finding("D1", "src/big.py")], 2: []})


def test_over_budget_invocation_is_marked(project: Path) -> None:
    review, fake = over_budget_run(project)
    result = micro.run_micro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    invocations: list[dict[str, Any]] = result.run_state.to_json()["invocations"]
    assert invocations
    assert all(
        item["budget_outcome"] == "exceeded_after_success" and item["max_budget_usd"] == 0.5
        for item in invocations
    )
    assert all("over_budget" not in item for item in invocations)
    totals = pipeline.summarize_invocations(as_run(result.run_state))
    assert len(totals["over_budget"]) == len(invocations)
    assert totals["over_budget"][0] == {
        "role": "discovery",
        "split_index": 0,
        "cost_usd": 0.9,
        "max_budget_usd": 0.5,
        "outcome": "exceeded_after_success",
    }


def test_under_budget_invocation_is_not_marked(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    result = micro.run_micro(
        AssemblyInput.load(review), settings_for(MicroFake({1: [], 2: []}), max_budget_usd=0.5)
    )
    assert all("budget_outcome" not in item for item in result.run_state.to_json()["invocations"])


def test_run_output_lists_over_budget_roles(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review, fake = over_budget_run(project)
    assert main(["run", str(review.id), "micro"], invoke=fake) == EXIT_OK
    out = capsys.readouterr().out
    assert "上限超過: 発見役（シャード 0）が 0.9000 USD（上限 " in out
    assert main(["run", str(review.id), "micro", "--json"], invoke=fake) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["over_budget"][0]["role"] == "discovery"
    assert payload["run"]["invocations"][0]["budget_outcome"] == "exceeded_after_success"


def test_show_json_carries_over_budget_for_the_run_band(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review, fake = over_budget_run(project)
    assert main(["run", str(review.id), "micro"], invoke=fake) == EXIT_OK
    capsys.readouterr()
    assert main(["show", str(review.id), "micro", "--json"]) == EXIT_OK
    run = json.loads(capsys.readouterr().out)["run"]
    assert run["over_budget"][0]["cost_usd"] == 0.9
    assert run["over_budget"][0]["max_budget_usd"] == 0.5


def test_run_output_lists_stopped_roles(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    big, small = shard_costs(review)
    fake = MicroFake({1: [], 2: []})
    budget = f"{big + small:.6f}"
    code = main(
        ["run", str(review.id), "micro", "--model", "m", "--max-budget-usd-per-call", budget],
        invoke=fake,
    )
    assert code != EXIT_PROCESSING_FAILURE
    assert "起動せず: 発見役（シャード 0）、入力の見積もり " in capsys.readouterr().out


def test_policy_group_keeps_limit_without_margin(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([])
    macro.run_macro(
        AssemblyInput.load(review),
        with_budget(
            settings_for(fake, max_budget_usd=0.5), model="claude-sonnet-5-5", reserve_margin=False
        ),
    )
    assert fake.calls[0].budget.cli_usd == 0.5
    reserving = settings_for(FakeInvoke(), max_budget_usd=0.5)
    not_reserving = with_budget(reserving, reserve_margin=False)
    check = input_budget.check_input_budget("a" * 100_000, not_reserving)
    assert check.stop is None
    assert input_budget.check_input_budget("a" * 100_000, reserving).stop is not None


def test_check_and_invocation_follow_the_same_reserve_margin() -> None:
    # 方針の群の設定は余裕を引かず、実装の群の設定は引く。検査と起動は同じ欄から決まる。
    prompt = "a" * 30_000
    for group, reserves in ((MACRO, False), (MICRO, True)):
        defaults = group_defaults(group)
        assert defaults.reserve_margin is reserves
        settings = with_budget(
            settings_for(FakeInvoke(), max_budget_usd=0.5), reserve_margin=defaults.reserve_margin
        )
        invocation = settings.invocation(prompt=prompt, json_schema={}, working_directory=Path())
        assert (invocation.budget.cli_usd == 0.5) is (not reserves)
        assert (input_budget.check_input_budget("a" * 100_000, settings).stop is None) is (
            not reserves
        )
