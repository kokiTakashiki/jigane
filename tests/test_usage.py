import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFake, discovered, settings_for

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.output import warning_lines
from jigane.commands.subcommands import usage
from jigane.commands.subcommands.show import run_record
from jigane.findings.records import Triage, load_history_checked
from jigane.groups.definitions import MICRO
from jigane.groups.policy_runs import PolicyRole
from jigane.llm_runs.usage_table import (
    INPUT_SIDE_TOKEN_FIELDS,
    UsageField,
)
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import adversarial, macro
from jigane.skills.assembly import AssemblyInput


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def invocation(role: str, **overrides: str | float) -> dict[str, Any]:
    return {
        "role": role,
        "split_index": None,
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 1000,
        "cost_usd": 0.5,
        "duration_ms": 2000,
        "num_turns": 3,
        "is_error": False,
        "terminal_reason": None,
        "permission_denials": 0,
        **overrides,
    }


def write_fixture(review: Review) -> None:
    directory = review.id.directory()
    runs = {
        "micro": {
            "model": "m",
            "invocations": [
                invocation("discovery", prompt_version="aaaaaaaaaaaa", prompt_chars=1000),
                invocation("discovery", prompt_version="aaaaaaaaaaaa", prompt_chars=2000),
                invocation("discovery"),
                invocation(
                    "judge", prompt_version="bbbbbbbbbbbb", prompt_chars=500, cost_usd=0.25
                ),
            ],
        }
    }
    findings = {
        "last_number": 3,
        "groups": {
            "micro": [
                {"id": "F1", "triage": "must_read"},
                {"id": "F2", "triage": "folded"},
                {"id": "F3", "triage": "rejected"},
            ]
        },
    }
    (directory / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    (directory / "findings.json").write_text(json.dumps(findings), encoding="utf-8")


def usage_json(review: Review, capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    assert main(["usage", str(review.id), "--json"]) == EXIT_OK
    rows: list[dict[str, Any]] = json.loads(capsys.readouterr().out)["rows"]
    return rows


def test_prompt_version_is_stable_and_follows_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    first = adversarial.prompt_version("rebuttal")
    assert first == adversarial.prompt_version("rebuttal")
    assert len(first) == 12
    texts = {"common": "共通", "rebuttal": "反証", "verifier_scope_rule": "規則"}
    monkeypatch.setattr(adversarial, "template_text", lambda name: texts[name])
    before = adversarial.prompt_version("rebuttal")
    texts["rebuttal"] = "反証を直した"
    assert adversarial.prompt_version("rebuttal") != before
    texts["rebuttal"] = "反証"
    texts["common"] = "共通を直した"
    assert adversarial.prompt_version("rebuttal") != before


def test_prompt_chars_equals_rendered_length() -> None:
    rendered = adversarial.render_prompt(
        "rebuttal", "変更", candidates_json=[{"index": 0}], challenges_json=[]
    )
    assert rendered.stamp.chars == len(rendered.text)
    assert rendered.stamp.version == adversarial.prompt_version("rebuttal")


def test_run_records_prompt_version_and_chars(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake([discovered("M2")])
    macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    records = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))["macro"][
        "invocations"
    ]
    assert [r["role"] for r in records] == [PolicyRole.DIAGRAM]
    prompt = fake.calls[0].prompt
    assert records[0]["prompt_chars"] == len(prompt)
    assert records[0]["prompt_version"] == macro.stamp_diagram_prompt(prompt).version


def test_usage_writes_a_diagram_run_as_a_diagram_row_in_csv(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    runs = {"macro": {"model": "m", "shape": "diagram", "invocations": [invocation("diagram")]}}
    (review.id.directory() / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    assert main(["usage", str(review.id), "--csv"]) == EXIT_OK
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert [(r["group"], r["role"]) for r in rows] == [("方針", "図"), ("方針", "合計")]


def test_usage_reads_past_single_and_full_runs(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    roles = ["single", "discovery:route", "discovery:verify", "discovery:purpose", "conclude"]
    runs = {
        "macro": {"model": "m", "shape": "full", "invocations": [invocation(r) for r in roles]}
    }
    (review.id.directory() / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    rows = usage_json(review, capsys)
    assert {r["role"] for r in rows if r["role"] != "合計"} == {
        "一回の形（廃止）",
        "四回の形（廃止）:道筋",
        "四回の形（廃止）:検証",
        "四回の形（廃止）:目的",
        "四回の形（廃止）:結論",
    }


def test_usage_aggregates_rows(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    rows = usage_json(review, capsys)
    assert [(r["group"], r["role"], r["prompt_version"]) for r in rows] == [
        ("実装", "発見", None),
        ("実装", "発見", "aaaaaaaaaaaa"),
        ("実装", "判定", "bbbbbbbbbbbb"),
        ("実装", "合計", None),
    ]
    old, versioned, judge, total = rows
    assert (old["calls"], old["prompt_chars"]) == (1, None)
    assert (versioned["calls"], versioned["prompt_chars"], versioned["turns"]) == (2, 1500, 6)
    assert versioned["input_tokens"] == 20
    assert versioned["cache_creation_tokens"] == 2000
    assert versioned["cache_read_tokens"] == 200
    assert versioned["output_tokens"] == 40
    assert versioned["cost_usd"] == 1.0
    assert versioned["duration_s"] == 4.0
    assert judge["cost_usd"] == 0.25
    assert (total["calls"], total["cost_usd"], total["duration_s"]) == (4, 1.75, 8.0)
    assert (total["findings_total"], total["must_read"], total["folded"], total["rejected"]) == (
        3,
        1,
        1,
        1,
    )
    assert total["remaining"] == 0
    assert total["usd_per_must_read"] == 1.75


def test_usd_per_must_read_is_empty_without_must_read(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    path = review.id.directory() / "findings.json"
    state = json.loads(path.read_text("utf-8"))
    state["groups"]["micro"] = []
    path.write_text(json.dumps(state), encoding="utf-8")
    total = usage_json(review, capsys)[-1]
    assert total["must_read"] == 0
    assert total["usd_per_must_read"] is None


def test_csv_header_and_no_header(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    assert main(["usage", str(review.id), "--csv"]) == EXIT_OK
    with_header = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert with_header[0][:5] == ["group", "role", "model", "prompt_version", "prompt_chars"]
    assert with_header[0][18:20] == ["usd_per_must_read", "effort"]
    assert with_header[0][20:] == [
        "discovered",
        "discarded",
        "discard_ratio",
        "discard_state",
        "unverified",
    ]
    assert main(["usage", str(review.id), "--csv", "--no-header"]) == EXIT_OK
    without_header = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert without_header == with_header[1:]


def test_table_has_japanese_headings(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    assert main(["usage", str(review.id)]) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("群")
    assert "合計" in out[-1]


def test_default_table_is_compact_and_wide_shows_every_column(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    assert main(["usage", str(review.id)]) == EXIT_OK
    compact = capsys.readouterr().out.splitlines()[0]
    assert "トークン合計" in compact
    assert "キャッシュ作成" not in compact
    assert main(["usage", str(review.id), "--wide"]) == EXIT_OK
    wide = capsys.readouterr().out.splitlines()[0]
    assert "キャッシュ作成" in wide
    assert "トークン合計" not in wide


def test_usage_shows_effort(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    assert {row["effort"] for row in usage_json(review, capsys)} == {None}

    runs_path = review.id.directory() / "runs.json"
    runs = json.loads(runs_path.read_text("utf-8"))
    runs["micro"]["effort"] = "high"
    runs_path.write_text(json.dumps(runs), encoding="utf-8")
    assert {row["effort"] for row in usage_json(review, capsys)} == {"high"}

    assert main(["usage", str(review.id), "--csv"]) == EXIT_OK
    header, first = list(csv.reader(io.StringIO(capsys.readouterr().out)))[:2]
    assert first[header.index("effort")] == "high"

    assert main(["usage", str(review.id), "--wide"]) == EXIT_OK
    assert "effort" in capsys.readouterr().out


def test_usage_total_uses_elapsed_time_when_recorded(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    # 記録の無い古い実行では、役ごとの行の合算（4 起動 x 2 秒）
    assert usage_json(review, capsys)[-1]["duration_s"] == 8.0

    runs_path = review.id.directory() / "runs.json"
    runs = json.loads(runs_path.read_text("utf-8"))
    runs["micro"]["elapsed_s"] = 3.5
    runs_path.write_text(json.dumps(runs), encoding="utf-8")
    rows = usage_json(review, capsys)
    assert rows[-1]["duration_s"] == 3.5
    assert rows[0]["duration_s"] == 2.0


def test_show_without_elapsed_time_reports_null(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    assert run_record(review, MICRO) is not None
    assert run_record(review, MICRO)["elapsed_s"] is None  # type: ignore[index]
    assert main(["show", str(review.id), "micro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["run"]["elapsed_s"] is None


def test_triage_columns_keep_their_names_and_order() -> None:
    names = [column.name for column in usage.COLUMNS]
    start = names.index("findings_total") + 1
    assert names[start : start + 5] == [
        "must_read",
        "remaining",
        "folded",
        "rejected",
        "usd_per_must_read",
    ]
    assert {c.name: c.heading for c in usage.COLUMNS}["folded"] == "折りたたみ"


# 保存済みの usage.csv が頼る列の並び。列を足すときは COLUMNS の末尾に足し、ここは変えない
PERSISTED_COLUMNS = (
    "group",
    "role",
    "model",
    "prompt_version",
    "prompt_chars",
    "calls",
    "turns",
    "input_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "output_tokens",
    "cost_usd",
    "duration_s",
    "findings_total",
    "must_read",
    "remaining",
    "folded",
    "rejected",
    "usd_per_must_read",
    "effort",
)


# unverified の区分の列を足す前の、考えた候補の列の並び
CONSIDERED_COLUMN_NAMES = ("discovered", "discarded", "discard_ratio", "discard_state")


def test_columns_keep_the_persisted_prefix() -> None:
    names = tuple(column.name for column in usage.COLUMNS)
    assert names[: len(PERSISTED_COLUMNS)] == PERSISTED_COLUMNS


def test_only_out_of_scope_is_left_out_of_the_usage_columns() -> None:
    assert len(set(usage.ALL_TRIAGE_COLUMNS)) == len(usage.ALL_TRIAGE_COLUMNS)
    assert set(Triage) - set(usage.ALL_TRIAGE_COLUMNS) == {Triage.OUT_OF_SCOPE}
    column_names = {column.name for column in usage.COLUMNS}
    assert {t.value for t in usage.ALL_TRIAGE_COLUMNS} <= column_names
    assert Triage.OUT_OF_SCOPE.value not in column_names


def corrupt_history(review: Review) -> None:
    (review.id.directory() / "findings_history.json").write_text("{not json", encoding="utf-8")


def test_usage_json_carries_history_warnings_and_does_not_log(
    project: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    corrupt_history(review)
    assert main(["usage", str(review.id), "--json"]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    [warning] = document["history_warnings"]
    expected = load_history_checked(review.id.directory()).warnings[0].to_json()
    assert warning == expected
    assert document["rows"]
    assert caplog.records == []


def test_usage_table_puts_the_history_warning_first_and_does_not_log(
    project: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    corrupt_history(review)
    assert main(["usage", str(review.id)]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    expected = warning_lines(load_history_checked(review.id.directory()).warnings)
    assert lines[: len(expected)] == expected
    assert lines[len(expected)].startswith("群")
    assert caplog.records == []


def test_usage_json_has_no_history_warning_for_a_readable_history(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    assert main(["usage", str(review.id), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["history_warnings"] == []


# 既定の表の列。トークンは 4 種の合計を 1 列で出し、内訳の列と model、effort は --wide だけが出す
COMPACT_COLUMN_NAMES = (
    "group",
    "role",
    "prompt_version",
    "calls",
    "total_tokens",
    "cost_usd",
    "findings_total",
    "must_read",
    "usd_per_must_read",
    "discovered",
    "discarded",
    "discard_ratio",
)
COMPACT_ONLY_NAMES = {"total_tokens"}
WIDE_ONLY_NAMES = {
    "model",
    "prompt_chars",
    "turns",
    "input_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "output_tokens",
    "duration_s",
    "remaining",
    "folded",
    "rejected",
    "effort",
    "unverified",
    "discard_state",
}
COMPACT_HEADINGS = (
    "群",
    "役",
    "プロンプト版",
    "起動",
    "トークン合計",
    "金額USD",
    "指摘",
    "必読",
    "必読1件あたりUSD",
    "発見",
    "捨てた",
    "捨てた割合",
)
WIDE_HEADINGS = (
    "群",
    "役",
    "モデル",
    "プロンプト版",
    "文字数",
    "起動",
    "ターン",
    "入力",
    "キャッシュ作成",
    "キャッシュ読出",
    "出力",
    "金額USD",
    "秒",
    "指摘",
    "必読",
    "残り",
    "折りたたみ",
    "棄却",
    "必読1件あたりUSD",
    "effort",
    "発見",
    "捨てた",
    "捨てた割合",
    "捨てた割合の状態",
    "未検証",
)


def test_compact_columns_keep_their_order() -> None:
    assert tuple(column.name for column in usage.COMPACT_COLUMNS) == COMPACT_COLUMN_NAMES


def test_wide_and_csv_columns_keep_their_order_because_stored_rows_depend_on_it() -> None:
    # CSV の列の順は保存済みの行（docs/reviews/usage.csv）が頼る永続の契約
    assert tuple(column.name for column in usage.COLUMNS) == (
        *PERSISTED_COLUMNS,
        *CONSIDERED_COLUMN_NAMES,
        "unverified",
    )


def test_columns_before_the_appended_triage_match_the_order_before_unverified_existed() -> None:
    names = tuple(column.name for column in usage.COLUMNS)
    before_unverified = (*PERSISTED_COLUMNS, *CONSIDERED_COLUMN_NAMES)
    assert names[: len(before_unverified)] == before_unverified


def test_display_assignment_of_each_column() -> None:
    by_display = {
        display: {c.name for c in usage._DEFINITIONS if c.display is display}
        for display in usage.Display
    }
    assert by_display[usage.Display.COMPACT_ONLY] == COMPACT_ONLY_NAMES
    assert by_display[usage.Display.WIDE_ONLY] == WIDE_ONLY_NAMES
    assert by_display[usage.Display.ALWAYS] == set(COMPACT_COLUMN_NAMES) - COMPACT_ONLY_NAMES


def test_usage_output_headings_follow_the_column_assignment(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_fixture(review)
    assert main(["usage", str(review.id)]) == EXIT_OK
    assert tuple(capsys.readouterr().out.splitlines()[0].split()) == COMPACT_HEADINGS
    assert main(["usage", str(review.id), "--wide"]) == EXIT_OK
    assert tuple(capsys.readouterr().out.splitlines()[0].split()) == WIDE_HEADINGS
    assert main(["usage", str(review.id), "--csv"]) == EXIT_OK
    header = capsys.readouterr().out.splitlines()[0]
    assert tuple(header.split(",")) == tuple(column.name for column in usage.COLUMNS)


def test_shared_usage_fields_match_the_columns_the_writer_defines() -> None:
    # 読む側が使う列名は UsageField から引くので、書く側の列と食い違えばここで落ちる
    # 区分の列の名前は Triage が持ち、UsageField には含めない
    shared = {field.value for field in UsageField}
    triage_names = {t.value for t in usage.ALL_TRIAGE_COLUMNS}
    assert shared == {c.name for c in usage._DEFINITIONS} - triage_names
    assert {c.name for c in usage.COLUMNS} <= shared | triage_names
    for field in INPUT_SIDE_TOKEN_FIELDS:
        assert field.value in {c.name for c in usage.COLUMNS}
