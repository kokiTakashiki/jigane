import json
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFailure, MacroFake, as_run, discovered
from keep_previous_support import REASON, extras, first_run
from keep_previous_support import settings as keep_settings

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_INCOMPLETE_COVERAGE, EXIT_OK
from jigane.commands.subcommands.run import failure_reason_lines, retry_lines
from jigane.commands.views.findings import over_budget_lines
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro, pipeline
from jigane.skills.assembly import AssemblyInput


def record(
    stage: str, index: int, kind: str, reason: str, attempt: int, disposition: str
) -> dict[str, Any]:
    return {
        "stage": stage,
        "index": index,
        "role": "discovery" if stage == "discovery" else "judge",
        "kind": kind,
        "reason": reason,
        "attempt": attempt,
        "disposition": disposition,
        "scope": "launch",
        "round": "current",
        "ran_at": None,
    }


def test_retry_line_appears_only_when_a_split_was_rerun() -> None:
    assert retry_lines(as_run({"launch_failures": []}).launch_failures or ()) == []
    assert retry_lines(as_run({}).launch_failures or ()) == []
    state = {
        "launch_failures": [
            record("verification", 0, "overloaded", "529", 1, "recovered"),
            record("verification", 1, "timeout", "t", 1, "rerun_failed"),
            record("verification", 1, "timeout", "t", 2, "not_rerun"),
            record("discovery", 3, "timeout", "t", 1, "not_rerun"),
        ]
    }
    assert retry_lines(as_run(state).launch_failures or ()) == [
        "走らせ直した検証の分割: 2 件（一時の失敗）"
    ]


def test_failure_lines_show_only_the_final_failure_of_each_launch() -> None:
    state = {
        "launch_failures": [
            record("discovery", 2, "bad_output", "schema", 1, "not_rerun"),
            record("verification", 0, "overloaded", "529", 1, "recovered"),
            record("verification", 1, "timeout", "first", 1, "rerun_failed"),
            record("verification", 1, "timeout", "second", 2, "not_rerun"),
        ],
    }
    assert failure_reason_lines(as_run(state).launch_failures or ()) == [
        "  発見役 2（bad_output）: schema",
        "  検証の分割 1・judge（timeout、2 回目）: second",
    ]


def test_input_stops_are_shown_once_from_budget_stops_for_both_stages() -> None:
    stop = {
        "estimated_input_tokens": 900,
        "estimated_input_usd": 1.5,
        "max_budget_usd": 1.0,
    }
    state = {
        "launch_failures": [],
        "budget_stops": [
            {**stop, "role": "discovery", "index": 0},
            {**stop, "role": "rebuttal", "index": 1},
        ],
    }
    # 失敗の理由の行には出ず、起動せずの行にだけ、発見役と検証役が同じ形で出る
    assert failure_reason_lines(as_run(state).launch_failures or ()) == []
    lines = over_budget_lines(pipeline.summarize_invocations(as_run(state)))
    assert len(lines) == 2
    assert all(line.startswith("起動せず: ") for line in lines)


def test_missing_candidate_reruns_are_reported_apart_from_split_reruns() -> None:
    refill = {
        **record("verification", 1, "bad_output", "index [1] が無い", 2, "rerun_failed"),
        "scope": "missing_candidates",
        "candidates": [],
    }
    # 同じ分割に NOT_RERUN の起動の失敗があっても、欠けた候補の失敗は理由を落とさない
    ended = record("verification", 1, "timeout", "t", 2, "not_rerun")
    state = {"launch_failures": [refill, ended]}
    assert retry_lines(as_run(state).launch_failures or ()) == [
        "欠けた候補だけ走らせ直した検証の役: 1 件"
    ]
    assert failure_reason_lines(as_run(state).launch_failures or ()) == [
        "  検証の分割 1・judge（bad_output、欠けた候補の走らせ直し）: index [1] が無い",
        "  検証の分割 1・judge（timeout、2 回目）: t",
    ]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_show_reports_that_the_result_was_not_replaced(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = first_run(project)
    macro.run_macro(
        AssemblyInput.load(review),
        keep_settings(MacroFake([discovered("P1")], fails=MacroFailure.DIAGRAM)),
        extras=extras(),
    )
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    assert f"図の起動が失敗した{REASON}" in capsys.readouterr().out
    macro.run_macro(
        AssemblyInput.load(review),
        keep_settings(MacroFake([discovered("P1")]), diagram_usd=0.0001),
        extras=extras(),
    )
    capsys.readouterr()
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert {item["viewpoint"] for item in shown["findings"]} == {"M4", "P1"}
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    assert f"図の起動が入力の上限のために見送られた{REASON}" in capsys.readouterr().out


@pytest.mark.usefixtures("all_principle_shapes")
def test_run_reports_that_the_result_was_not_replaced(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    first = MacroFake([discovered("M4"), discovered("P1")])
    assert main(["run", "shop:p", "macro", "--json"], invoke=first) == EXIT_OK
    capsys.readouterr()
    second = MacroFake([discovered("P1")], fails=MacroFailure.DIAGRAM)
    assert main(["run", "shop:p", "macro", "--json"], invoke=second) == EXIT_INCOMPLETE_COVERAGE
    captured = capsys.readouterr()
    assert REASON in captured.err
    assert REASON in json.loads(captured.out)["notice"]


@pytest.mark.usefixtures("all_principle_shapes")
def test_first_run_failed_diagram_says_nothing_was_kept_in_run_and_show(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    assert (
        main(
            ["run", "shop:p", "macro", "--json"], invoke=MacroFake([], fails=MacroFailure.DIAGRAM)
        )
        == EXIT_INCOMPLETE_COVERAGE
    )
    captured = capsys.readouterr()
    assert REASON not in captured.err
    assert REASON not in captured.out
    capsys.readouterr()
    main(["show", "shop:p", "macro"])
    assert REASON not in capsys.readouterr().out


def test_first_run_stopped_diagram_says_nothing_was_kept_in_run_and_show(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    result = macro.run_macro(
        AssemblyInput.load(review),
        keep_settings(MacroFake([discovered("P1")]), diagram_usd=0.0001),
        extras=extras(),
    )
    assert REASON not in (result.notice or "")
    capsys.readouterr()
    main(["show", "shop:p", "macro"])
    assert REASON not in capsys.readouterr().out
