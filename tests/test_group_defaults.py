import json
from pathlib import Path

import pytest
from fakes import MacroFake, MicroFake, clear_policy, d_finding, discovered
from test_security import SecurityFake, s_finding, write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.groups.definitions import Effort
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review, create_feature_review


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def review_of(project: Path) -> Review:
    return create_feature_review(project, ["src"], "p")


def settings(calls: list[claude.Invocation]) -> set[tuple[str, str | None, float]]:
    return {(c.model, c.effort, c.budget.cli_usd) for c in calls}


def reserved_settings(calls: list[claude.Invocation]) -> set[tuple[str, str | None]]:
    """CLI に渡す上限は余裕を引いた値になる（上限の半分より大きく、上限未満）。"""
    assert all(0.25 < c.budget.cli_usd < 0.5 for c in calls)
    return {(c.model, c.effort) for c in calls}


def test_macro_defaults_reach_every_invocation(project: Path) -> None:
    review = review_of(project)
    fake = MacroFake([discovered("M2")])
    arguments = ["run", str(review.id), "macro"]
    assert main(arguments, invoke=fake) == EXIT_OK
    assert len(fake.calls) == 1
    assert settings(fake.calls) == {("claude-sonnet-5-5", "high", 0.5)}
    runs = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))
    assert runs["macro"]["model"] == "claude-sonnet-5-5"
    assert runs["macro"]["effort"] == "high"


def test_micro_defaults(project: Path) -> None:
    review = review_of(project)
    clear_policy(review)
    fake = MicroFake({1: [d_finding("D1")]})
    assert main(["run", str(review.id), "micro"], invoke=fake) == EXIT_OK
    assert reserved_settings(fake.calls) == {("claude-sonnet-5-5", None)}
    runs = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))
    assert runs["micro"]["effort"] is None


def test_security_defaults(project: Path) -> None:
    review = review_of(project)
    write_mcp()
    fake = SecurityFake({1: [s_finding()]})
    assert main(["run", str(review.id), "security"], invoke=fake) == EXIT_OK
    assert reserved_settings(fake.calls) == {("claude-sonnet-5-5", None)}


def test_overrides_apply_to_every_role(project: Path) -> None:
    review = review_of(project)
    fake = MacroFake([discovered("M2")])
    argv = [
        "run",
        str(review.id),
        "macro",
        "--model",
        "claude-sonnet-5-5",
        "--effort",
        "low",
        "--max-budget-usd-per-call",
        "0.5",
    ]
    assert main(argv, invoke=fake) == EXIT_OK
    assert settings(fake.calls) == {("claude-sonnet-5-5", "low", 0.5)}


def test_effort_override_on_group_without_default(project: Path) -> None:
    review = review_of(project)
    clear_policy(review)
    fake = MicroFake({1: [d_finding("D1")]})
    assert main(["run", str(review.id), "micro", "--effort", "max"], invoke=fake) == EXIT_OK
    assert {c.effort for c in fake.calls} == {"max"}


def test_estimate_json_carries_the_resolved_effort(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = review_of(project)
    assert main(["estimate", str(review.id), "micro", "--effort", "low", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["effort"] == "low"
    with pytest.raises(SystemExit):
        main(["estimate", str(review.id), "micro", "--effort", "ultra"])


def test_command_carries_effort(tmp_path: Path) -> None:
    def invocation(effort: Effort | None) -> claude.Invocation:
        return claude.Invocation(
            prompt="p",
            json_schema={"type": "object"},
            working_directory=tmp_path,
            budget=claude.LaunchBudget(limit_usd=1.0, cli_usd=1.0),
            model="m",
            effort=effort,
        )

    with_effort = claude.build_command(invocation(Effort.HIGH))
    assert with_effort[with_effort.index("--effort") + 1] == "high"
    assert "--effort" not in claude.build_command(invocation(None))


@pytest.mark.parametrize(
    ("group", "model", "effort", "budget"),
    [
        ("macro", "claude-sonnet-5-5", "high", 0.5),
        ("micro", "claude-sonnet-5-5", None, 0.5),
        ("security", "claude-sonnet-5-5", None, 0.5),
    ],
)
def test_estimate_shows_group_settings(
    project: Path,
    capsys: pytest.CaptureFixture[str],
    group: str,
    model: str,
    effort: str | None,
    budget: float,
) -> None:
    review = review_of(project)
    assert main(["estimate", str(review.id), group, "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert (data["model"], data["effort"], data["max_budget_usd_per_call"]) == (
        model,
        effort,
        budget,
    )
    assert main(["estimate", str(review.id), group]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"モデルの既定値: {model}" in out
    assert f"effort: {effort or '渡さない'}" in out


def test_run_help_is_built_from_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = "".join(capsys.readouterr().out.split())
    assert "方針はclaude-sonnet-5-5" in out
    assert "方針はhigh、実装は渡さない、セキュリティは渡さない" in out
    assert "方針は0.5USD、実装は0.5USD、セキュリティは0.5USD" in out
    assert "方針は起動が1回なので、この値が実行全体の目安になります" in out
    assert "上限の目安" in out
    assert "超えることがあります" in out
    assert "方針はxhigh" not in out


def test_run_help_explains_exit_code_3_and_keeps_the_sentence_intact(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = capsys.readouterr().out
    # argparse の折り返しは語の間の空白を改行に変えるので、空白を除いて文として照合する
    flat = "".join(out.split())
    assert (
        "終了コード:0は正常終了、2は処理の失敗、3はシャードか検証の分割が失敗し、結果に未検証の候補を含むこと。"
        in flat
    )
    assert "終了コード:" in out


# 読む側が群を見分けるのは group の値で、鍵の有無ではない。群ごとの鍵の集合はここで固定する
COMMON_ESTIMATE_KEYS = {
    "group",
    "discovery_input_tokens",
    "past_run",
    "note",
    "model",
    "effort",
    "max_budget_usd_per_call",
    "run_guide",
    "history_warnings",
}
SHARDED_ESTIMATE_KEYS = {"policy_notice", "target_files", "shard_bytes"}
INPUT_COST_KEYS = {
    "input_cost_usd",
    "input_price_usd_per_million_tokens",
    "input_price_assumed",
    "exceeds_budget",
}
# 出力と推論の分の見込みは、設計書の形があるときだけ付く。
# skipped_usage_rows は、そのうえ使用量の表に壊れた行があるときだけ付く。
# expected.budget_warning は、そのうえ見込みが目安に達するときだけ付く。
# budget_stop_warning は、設計書の形に依らず exceeds_budget が真のときだけ付く
EXPECTED_KEYS = {
    "total_usd",
    "output_multiplier",
    "output_multiplier_source",
    "exceeds_budget",
}
OPTIONAL_EXPECTED_KEYS = {"skipped_usage_rows", "budget_warning"}
CONDITIONAL_KEYS = {"budget_stop_warning", "expected"}


@pytest.mark.parametrize(
    ("group", "specific_keys"),
    [
        (
            "macro",
            {"largest_sections", "shape", "materials_bytes", "planned_invocations"}
            | INPUT_COST_KEYS,
        ),
        (
            "micro",
            SHARDED_ESTIMATE_KEYS | {"prompt_bytes", "shard_input_cost_usd"} | INPUT_COST_KEYS,
        ),
        (
            "security",
            SHARDED_ESTIMATE_KEYS | {"prompt_bytes", "shard_input_cost_usd"} | INPUT_COST_KEYS,
        ),
    ],
)
def test_estimate_json_keys_are_fixed_per_group(
    project: Path,
    capsys: pytest.CaptureFixture[str],
    group: str,
    specific_keys: set[str],
) -> None:
    review = review_of(project)
    assert main(["estimate", str(review.id), group, "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert next(iter(data)) == "group"
    assert data["group"] == group
    assert set(data) - CONDITIONAL_KEYS == COMMON_ESTIMATE_KEYS | specific_keys
    # 改めた鍵の古い名前と、外した鍵は出ない
    assert not set(data) & {"blockers", "invocation_count", "origin"}
    if group == "macro" and data["shape"] == "design":
        assert EXPECTED_KEYS <= data["expected"].keys() <= EXPECTED_KEYS | OPTIONAL_EXPECTED_KEYS
    else:
        assert "expected" not in data
    assert data["run_guide"].keys() == {"review_kind", "text"}
    assert data["past_run"] is None or "invocation_count" in data["past_run"]
