import json
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFake, discovered, settings_for
from pr_support import commit_all

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.design.review_documents import create_design_doc_review
from jigane.groups import policy_runs
from jigane.groups.definitions import (
    DESIGN_SHAPE,
    GROUP_BY_ID,
    MACRO,
    MICRO,
    SECURITY,
    Effort,
)
from jigane.groups.policy_runs import PolicyRole, PolicyShape
from jigane.llm_runs import claude
from jigane.llm_runs.defaults import resolve_run_defaults
from jigane.llm_runs.usage_table import USAGE_CSV
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput
from jigane.skills.estimate_result import DesignPolicyEstimate, DiagramPolicyEstimate
from jigane.skills.output_estimate import (
    DEFAULT_OUTPUT_MULTIPLIER,
    MultiplierSource,
    SkippedRows,
    SkipReason,
    estimate_output,
    past_run_multiplier,
    read_usage_csv,
    skipped_rows_message,
)
from jigane.storage.store import data_home

DOCUMENT_PATH = "docs/shop-design.md"


def estimate_of(review: Review) -> dict[str, Any]:
    return macro.estimate_macro(AssemblyInput.load(review)).to_record()


def run_of(review: Review, settings: claude.RunSettings) -> macro.MacroRun:
    return macro.run_macro(AssemblyInput.load(review), settings)


USAGE_HEADER = (
    "date,review,jigane_commit,group,role,model,prompt_version,prompt_chars,calls,turns,"
    "input_tokens,cache_creation_tokens,cache_read_tokens,output_tokens,cost_usd\n"
)


@pytest.fixture
def repo(repo_template: dict[str, str], tmp_path: Path) -> Path:
    root = tmp_path / "work"
    shutil.copytree(repo_template["root"], root, symlinks=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / DOCUMENT_PATH).write_text(
        "# 題\n\n## 1. 序論\n\n" + "段落。\n" * 100, encoding="utf-8"
    )
    commit_all(root, "add design")
    return root


def write_settings(document: dict[str, object]) -> None:
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps(document), encoding="utf-8")


def test_shape_defaults_come_from_the_policy_shape_and_keep_the_current_values() -> None:
    for shape in (PolicyShape.DIAGRAM, PolicyShape.DESIGN):
        assert shape.run_shape.max_budget_usd == 0.5
        assert shape.run_shape.effort == "high"


def test_every_default_value_is_unchanged_for_every_group_and_shape() -> None:
    assert [
        (
            resolve_run_defaults(group, None, None).effort,
            resolve_run_defaults(group, None, None).budget.max_budget_usd,
        )
        for group in (MACRO, MICRO, SECURITY)
    ] == [("high", 0.5), (None, 0.5), (None, 0.5)]
    for shape in PolicyShape:
        assert resolve_run_defaults(MACRO, shape, None) == resolve_run_defaults(
            MACRO, PolicyShape.DIAGRAM, None
        )
        assert (shape.run_shape.effort, shape.run_shape.max_budget_usd) == ("high", 0.5)


def test_model_override_replaces_the_group_default_model() -> None:
    for group in (MACRO, MICRO, SECURITY):
        assert resolve_run_defaults(group, None, None, "m-override").budget.model == ("m-override")


def test_without_model_override_the_budget_uses_the_group_default_model() -> None:
    for group in (MACRO, MICRO, SECURITY):
        assert resolve_run_defaults(group, None, None).budget.model == GROUP_BY_ID[group].model


def test_resolution_order_is_argument_then_shape_then_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 形（0.7）と群（0.3）が違う値のときだけ、順が壊れると結果が変わる
    monkeypatch.setattr(policy_runs, "DESIGN_SHAPE", replace(DESIGN_SHAPE, max_budget_usd=0.7))
    group = GROUP_BY_ID[MACRO]
    monkeypatch.setitem(
        GROUP_BY_ID, MACRO, replace(group, shape=replace(group.shape, max_budget_usd=0.3))
    )
    assert resolve_run_defaults(MACRO, PolicyShape.DESIGN, None).budget.max_budget_usd == 0.7
    assert resolve_run_defaults(MACRO, None, None).budget.max_budget_usd == 0.3
    assert (
        resolve_run_defaults(
            MACRO, PolicyShape.DESIGN, None, max_budget_usd=0.9
        ).budget.max_budget_usd
        == 0.9
    )


def test_resolution_order_values_of_the_table() -> None:
    assert resolve_run_defaults(MACRO, PolicyShape.DESIGN, None).budget.max_budget_usd == 0.5
    assert resolve_run_defaults(MICRO, None, None).budget.max_budget_usd == 0.5
    assert resolve_run_defaults(MICRO, None, None).effort is None
    assert resolve_run_defaults(MACRO, PolicyShape.DESIGN, None).effort == "high"


SETTINGS_WITH_BUDGETS = {
    "macro": {"max_budget_usd": 0.7, "max_budget_usd_by_shape": {"design": 0.8}, "effort": "low"},
    "micro": {"max_budget_usd": 0.6, "effort": "max"},
    "security": {"max_budget_usd": 0.9},
}


def test_run_defaults_do_not_read_settings_json() -> None:
    plain = [
        resolve_run_defaults(group, shape, None)
        for group, shape in ((MACRO, PolicyShape.DESIGN), (MACRO, None), (MICRO, None))
    ]
    write_settings(SETTINGS_WITH_BUDGETS)
    assert [
        resolve_run_defaults(group, shape, None)
        for group, shape in ((MACRO, PolicyShape.DESIGN), (MACRO, None), (MICRO, None))
    ] == plain
    assert resolve_run_defaults(SECURITY, None, None).budget.max_budget_usd == 0.5
    assert resolve_run_defaults(MACRO, PolicyShape.DESIGN, None).effort == "high"


def test_settings_json_does_not_reach_the_launch(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    write_settings(SETTINGS_WITH_BUDGETS)
    fake = MacroFake([])
    assert main(["run", str(review.id), "macro"], invoke=fake) == EXIT_OK
    assert fake.calls[0].budget.limit_usd == 0.5


def test_the_flag_changes_the_limit(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    fake = MacroFake([])
    argv = ["run", str(review.id), "macro", "--max-budget-usd-per-call", "0.9"]
    assert main(argv, invoke=fake) == EXIT_OK
    assert fake.calls[0].budget.limit_usd == 0.9


def test_estimate_ignores_settings_json(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    write_settings(SETTINGS_WITH_BUDGETS)
    assert main(["estimate", str(review.id), "macro", "--json"]) == EXIT_OK
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["max_budget_usd_per_call"] == 0.5
    assert estimate["effort"] == "high"


def test_estimate_and_run_decide_the_limit_the_same_way(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    for flag in ([], ["--max-budget-usd-per-call", "0.9"]):
        fake = MacroFake([])
        assert main(["run", str(review.id), "macro", *flag], invoke=fake) == EXIT_OK
        capsys.readouterr()
        assert main(["estimate", str(review.id), "macro", "--json", *flag]) == EXIT_OK
        estimate = json.loads(capsys.readouterr().out)
        assert estimate["max_budget_usd_per_call"] == fake.calls[0].budget.limit_usd
    assert estimate["max_budget_usd_per_call"] == 0.9


def test_estimate_json_uses_the_group_limit_and_includes_the_output_part(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    assert main(["estimate", str(review.id), "macro", "--json", "--effort", "low"]) == EXIT_OK
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["max_budget_usd_per_call"] == 0.5
    assert estimate["effort"] == "low"
    expected = estimate["expected"]
    assert expected["output_multiplier_source"] == MultiplierSource.DEFAULT.value
    assert expected["output_multiplier"] == DEFAULT_OUTPUT_MULTIPLIER
    assert expected["total_usd"] == pytest.approx(
        estimate["input_cost_usd"] * DEFAULT_OUTPUT_MULTIPLIER
    )
    assert expected["total_usd"] > estimate["input_cost_usd"]
    assert expected["exceeds_budget"] is (expected["total_usd"] >= 0.5)
    assert ("budget_warning" in expected) is expected["exceeds_budget"]
    assert not set(estimate) & {
        "total_usd",
        "output_multiplier",
        "output_multiplier_source",
        "skipped_usage_rows",
        "exceeds_budget_expected",
        "expected_budget_warning",
    }


def test_diagram_shape_estimate_has_no_output_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    estimate = macro.estimate_macro(AssemblyInput.load(create_feature_review(root, ["src"], "p")))
    assert isinstance(estimate, DiagramPolicyEstimate)
    assert estimate.shape is PolicyShape.DIAGRAM
    assert estimate.expected_output() is None
    record = estimate.input_cost_record(
        claude.RunBudget("claude-sonnet-5-5", 100.0, reserve_margin=False)
    )
    assert "expected" not in record


def test_multiplier_comes_from_the_usage_csv_row_then_the_past_run(repo: Path) -> None:
    assert read_usage_csv(repo).multiplier is None
    path = repo / USAGE_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    # 入力 1,000,000 トークンは claude-sonnet-5-5 で 2 USD。実額 8 USD は倍率 4
    rows = [
        "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
        "d,r,c,方針,図,claude-sonnet-5-5,,,1,1,1000000,0,0,0,100.0\n",
        "d,r,c,実装,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,100.0\n",
    ]
    path.write_text(USAGE_HEADER + "".join(rows), encoding="utf-8")
    assert read_usage_csv(repo).multiplier == pytest.approx(4.0)
    assert past_run_multiplier(None) is None
    past = {"model": "claude-sonnet-5-5", "input_tokens": 1_000_000, "cost_usd": 6.0}
    assert past_run_multiplier(past) == pytest.approx(3.0)
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    estimate = macro.estimate_macro(AssemblyInput.load(review))
    assert isinstance(estimate, DesignPolicyEstimate)
    assert estimate.expected.source is MultiplierSource.USAGE_CSV
    assert estimate.expected.multiplier == pytest.approx(4.0)


GOOD_USAGE_ROW = "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n"


def write_usage(repo: Path, rows: list[str]) -> None:
    path = repo / USAGE_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(USAGE_HEADER + "".join(rows), encoding="utf-8")


def test_usage_csv_counts_only_broken_rows_and_bad_design_rows(repo: Path) -> None:
    write_usage(
        repo,
        [
            GOOD_USAGE_ROW,
            # 欄の足りない行と多すぎる行、group が空の行、role が空の行
            "d,r,c,方針,設計書,claude-sonnet-5-5\n",
            "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0,extra\n",
            "d,r,c,,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
            "d,r,c,方針,,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
            # 設計書の行の、数でない入力側のトークンと数でない金額
            "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,many,0,0,0,8.0\n",
            "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,free\n",
            # 過去の群、廃止した役、知らない語の行は有効な古い記録で、数えない
            "d,r,c,外観,単一,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
            "d,r,c,細部,発見:道筋,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
            "d,r,c,未知の群,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
            "d,r,c,方針,未知の役,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
            # 設計書の行でない行の数でない値は、倍率に使わないので数えない
            "d,r,c,方針,図,claude-sonnet-5-5,,,1,1,many,0,0,0,free\n",
            "d,r,c,方針,図,claude-sonnet-5-5,,,1,1,1000000,0,0,0,100.0\n",
        ],
    )
    reading = read_usage_csv(repo)
    # 倍率は、正しい設計書の行だけから今までと同じに出る
    assert reading.multiplier == pytest.approx(4.0)
    warning = skipped_rows_message(reading.skipped)
    assert warning is not None
    assert "6 行を倍率の根拠にしませんでした" in warning
    assert warning.startswith("警告")
    expected = estimate_output(repo, None, 1.0)
    assert expected.skipped_usage_rows == 6
    assert expected.multiplier == pytest.approx(4.0)
    assert expected.as_record().keys() == {
        "total_usd",
        "output_multiplier",
        "output_multiplier_source",
        "skipped_usage_rows",
    }
    assert expected.as_record()["skipped_usage_rows"] == 6


def test_usage_csv_with_only_matching_rows_gives_no_warning(repo: Path) -> None:
    write_usage(repo, [GOOD_USAGE_ROW, GOOD_USAGE_ROW.replace("設計書", "合計")])
    reading = read_usage_csv(repo)
    assert reading.multiplier == pytest.approx(4.0)
    assert skipped_rows_message(reading.skipped) is None


def test_run_notice_carries_the_skipped_usage_rows_warning(repo: Path) -> None:
    write_usage(repo, [GOOD_USAGE_ROW, "d,r,c,方針,設計書\n"])
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    prompt = macro.macro_plan(AssemblyInput.load(review)).build_prompt()
    fake = MacroFake([])
    settings = settings_for(fake, max_budget_usd=1_000_000)
    notice = macro.expected_budget_warning(review, PolicyShape.DESIGN, prompt, settings)
    assert notice is not None
    assert "1 行" in notice
    assert "出力と推論の分" not in notice


def test_run_warns_and_continues_when_the_expected_total_reaches_the_limit(repo: Path) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    prompt = macro.macro_plan(AssemblyInput.load(review)).build_prompt()
    tokens = claude.estimate_input_tokens(len(prompt.encode("utf-8")))
    input_usd = claude.input_cost_usd("m", tokens).usd
    # 入力だけなら上限の内で、倍率を掛けた見込みは上限に達する
    limit = input_usd * DEFAULT_OUTPUT_MULTIPLIER / 2
    fake = MacroFake([discovered("M2", DOCUMENT_PATH)])
    result = run_of(review, settings_for(fake, max_budget_usd=limit))
    assert len(fake.calls) == 1
    assert result.notice is not None
    assert "出力と推論の分" in result.notice
    assert "目安" in result.notice
    roomy = MacroFake([])
    quiet = run_of(review, settings_for(roomy, max_budget_usd=input_usd * 1_000_000))
    assert len(roomy.calls) == 1
    assert quiet.notice is None or "出力と推論の分" not in quiet.notice


def test_effort_option_comes_first_in_the_resolution_order() -> None:
    assert resolve_run_defaults(MACRO, PolicyShape.DESIGN, Effort.LOW).effort is Effort.LOW
    assert resolve_run_defaults(MICRO, None, Effort.MAX).effort is Effort.MAX
    assert resolve_run_defaults(MACRO, PolicyShape.DESIGN, None).effort is Effort.HIGH
    assert resolve_run_defaults(MICRO, None, None).effort is None


def test_estimate_shows_the_skipped_usage_rows_only_when_there_are_some(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_design_doc_review(repo, DOCUMENT_PATH)
    write_usage(repo, [GOOD_USAGE_ROW])
    assert main(["estimate", str(review.id), "macro"]) == EXIT_OK
    assert "倍率の根拠にしませんでした" not in capsys.readouterr().out
    assert main(["estimate", str(review.id), "macro", "--json"]) == EXIT_OK
    assert "skipped_usage_rows" not in json.loads(capsys.readouterr().out)["expected"]
    write_usage(repo, [GOOD_USAGE_ROW, "d,r,c,方針,設計書\n"])
    assert main(["estimate", str(review.id), "macro"]) == EXIT_OK
    warning = skipped_rows_message(SkippedRows(((SkipReason.BROKEN_COLUMNS, 1),)))
    assert warning is not None
    assert capsys.readouterr().out.splitlines().count(warning) == 1
    assert main(["estimate", str(review.id), "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["expected"]["skipped_usage_rows"] == 1


def test_each_skip_reason_is_counted_and_named_in_the_warning(repo: Path) -> None:
    write_usage(
        repo,
        [
            GOOD_USAGE_ROW,
            "d,r,c,方針,設計書,claude-sonnet-5-5\n",
            "d,r,c,,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
            "d,r,c,方針,,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n",
            "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,many,0,0,0,8.0\n",
            "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,many,0,0,0,9.0\n",
            "d,r,c,方針,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,free\n",
        ],
    )
    reading = read_usage_csv(repo)
    assert reading.skipped == SkippedRows(
        (
            (SkipReason.BROKEN_COLUMNS, 1),
            (SkipReason.EMPTY_LABEL, 2),
            (SkipReason.INVALID_VALUE, 3),
        )
    )
    assert skipped_rows_message(reading.skipped) == (
        "警告: docs/reviews/usage.csv の 6 行を倍率の根拠にしませんでした"
        "（欄の数が合わない 1 行、group か role が空の 2 行、"
        "設計書の行の値が欠けるか数でない 3 行）"
    )
    expected = estimate_output(repo, None, 1.0)
    assert expected.skipped_usage_rows == 6
    assert expected.as_record()["skipped_usage_rows"] == 6
    assert expected.as_record().keys() == {
        "total_usd",
        "output_multiplier",
        "output_multiplier_source",
        "skipped_usage_rows",
    }


def test_skipped_rows_from_counter_follows_definition_order_and_drops_zero() -> None:
    counter = Counter({SkipReason.INVALID_VALUE: 3, SkipReason.BROKEN_COLUMNS: 1})
    skipped = SkippedRows.from_counter(counter)
    assert skipped.counts == ((SkipReason.BROKEN_COLUMNS, 1), (SkipReason.INVALID_VALUE, 3))
    assert skipped.total == 4


def test_empty_skipped_rows_total_zero() -> None:
    assert SkippedRows().total == 0
    assert SkippedRows.from_counter(Counter()) == SkippedRows()


def test_a_skip_reason_without_rows_is_not_listed(repo: Path) -> None:
    write_usage(
        repo, [GOOD_USAGE_ROW, "d,r,c,,設計書,claude-sonnet-5-5,,,1,1,1000000,0,0,0,8.0\n"]
    )
    reading = read_usage_csv(repo)
    assert reading.skipped == SkippedRows(((SkipReason.EMPTY_LABEL, 1),))
    message = skipped_rows_message(reading.skipped)
    assert message is not None
    assert "欄の数が合わない" not in message
    assert "0 行" not in message


def test_every_policy_role_has_its_usage_label() -> None:
    assert {role: role.label for role in PolicyRole} == {
        PolicyRole.DIAGRAM: "図",
        PolicyRole.DESIGN: "設計書",
        PolicyRole.SINGLE: "一回の形（廃止）",
        PolicyRole.ROUTE: "四回の形（廃止）:道筋",
        PolicyRole.VERIFY: "四回の形（廃止）:検証",
        PolicyRole.PURPOSE: "四回の形（廃止）:目的",
        PolicyRole.CONCLUDE: "四回の形（廃止）:結論",
    }


def test_package_source_names_no_settings_file() -> None:
    package = Path(__file__).resolve().parents[1] / "jigane"
    named = [
        path.name
        for path in package.rglob("*.py")
        if "settings.json" in path.read_text(encoding="utf-8")
    ]
    assert named == []
