"""図の起動が走らない回の試験が共有する、偽の起動関数と設定と最初の回。"""

import json
from pathlib import Path
from typing import Any

from fakes import MacroFake, discovered, settings_for, with_budget

from jigane.findings.records import load_findings_state, triaged_dicts
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro, policy_launch
from jigane.skills.assembly import AssemblyInput

# 知らせの文面のうち、run と show のどちらにも出る部分
REASON = "ため、方針の指摘は置き換えず、前の回の結果を残しました"


def extras() -> list[policy_launch.ExtraLaunch]:
    return [policy_launch.ExtraLaunch("P1", "短い")]


def settings(
    fake: MacroFake, diagram_usd: float = 0.5, principle_usd: float = 0.5
) -> claude.RunSettings:
    base = with_budget(settings_for(fake, max_budget_usd=diagram_usd), model="claude-sonnet-5-5")
    principle = claude.RunBudget("claude-haiku-5-5", principle_usd, reserve_margin=True)
    return claude.RunSettings(
        budget=claude.RunBudget(
            base.budget.model,
            base.budget.max_budget_usd,
            base.budget.reserve_margin,
            {"P1": principle},
        ),
        effort=None,
        invoke=fake,
    )


def first_run(project: Path) -> Review:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M4"), discovered("P1")])
    macro.run_macro(AssemblyInput.load(review), settings(fake), extras=extras())
    return review


def stored(review: Review) -> list[dict[str, Any]]:
    state = load_findings_state(review.id.directory())
    return triaged_dicts(state.groups[macro.GROUP])


def runs_json(review: Review) -> dict[str, Any]:
    runs: dict[str, Any] = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))
    return runs
