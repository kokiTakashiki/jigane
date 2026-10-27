import json
from pathlib import Path
from typing import Any

import pytest
from store_format_support import check_upgrades

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.reviews.lifecycle import Review, ReviewError, create_feature_review
from jigane.reviews.selection import _UPGRADES, SELECTION_FILE, SELECTION_FORMAT, load_selection


def finding(number: int, verdict: str = "kept", theme: str | None = None) -> dict[str, Any]:
    return {
        "id": f"F{number}",
        "severity": "warning",
        "group": "micro",
        "viewpoint": "D1",
        "claim": f"claim{number}",
        "evidence": [{"path": "src/a.py", "start_line": 1, "end_line": 2}],
        "adjudication": {
            "kind": "rebuttal",
            "challenge": "c",
            "recheck": [],
            "verdict": verdict,
            "reason": "r",
        },
        "theme": theme,
        "triage": "rejected" if verdict == "rejected" else "must_read",
        "judgment_region": None,
    }


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n" + "x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    created = create_feature_review(root, ["src"], "p")
    state = {
        "last_number": 4,
        "last_theme_number": 1,
        "stale_groups": [],
        "groups": {
            "micro": [finding(1, theme="B1"), finding(2, theme="B1"), finding(3, "rejected")]
        },
        "themes": [
            {
                "id": "B1",
                "title": "t",
                "finding_ids": ["F1", "F2"],
                "severity": "warning",
                "triage": "must_read",
            }
        ],
    }
    (created.id.directory() / "findings.json").write_text(json.dumps(state), encoding="utf-8")
    return created


def run(*args: str) -> int:
    return main(list(args))


def selection_of(review: Review) -> dict[str, Any]:
    return load_selection(review.id.directory()).to_dict()


def test_pick_expands_theme_and_clears_hold(review: Review) -> None:
    name = str(review.id)
    assert run("hold", name, "F2") == EXIT_OK
    assert selection_of(review)["held"] == ["F2"]
    assert run("pick", name, "B1", "F1") == EXIT_OK
    assert selection_of(review) == {"picked": ["F1", "F2"], "held": [], "objections": {}}


def test_hold_removes_from_picked(review: Review) -> None:
    name = str(review.id)
    run("pick", name, "F1", "F2")
    run("hold", name, "F1")
    assert selection_of(review)["picked"] == ["F2"]
    assert selection_of(review)["held"] == ["F1"]


def test_unpick(review: Review) -> None:
    name = str(review.id)
    run("pick", name, "B1")
    assert run("unpick", name, "F1") == EXIT_OK
    assert selection_of(review)["picked"] == ["F2"]
    assert run("unpick", name, "F2") == EXIT_OK
    assert selection_of(review)["picked"] == []


def test_unknown_number_changes_nothing(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    name = str(review.id)
    run("pick", name, "F1")
    for command in ("pick", "unpick", "hold"):
        assert run(command, name, "F2", "F99") == EXIT_PROCESSING_FAILURE
    assert run("pick", name, "B9") == EXIT_PROCESSING_FAILURE
    assert selection_of(review) == {"picked": ["F1"], "held": [], "objections": {}}
    assert "F99" in capsys.readouterr().err


def test_pick_rejected_warns(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("pick", str(review.id), "F3") == EXIT_OK
    captured = capsys.readouterr()
    assert "棄却" in captured.err
    assert "F3" in captured.err
    assert selection_of(review)["picked"] == ["F3"]


def test_object(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    name = str(review.id)
    assert run("object", name, "F1", "--reason", "誤り") == EXIT_OK
    assert run("object", name, "F1", "--reason", "やはり誤り") == EXIT_OK
    assert selection_of(review)["objections"] == {"F1": "やはり誤り"}
    assert run("object", name, "F1", "--reason", "  ") == EXIT_PROCESSING_FAILURE
    assert run("object", name, "F99", "--reason", "x") == EXIT_PROCESSING_FAILURE
    assert selection_of(review)["objections"] == {"F1": "やはり誤り"}
    capsys.readouterr()


def test_unobject(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    name = str(review.id)
    run("object", name, "F1", "--reason", "誤り")
    run("object", name, "F2", "--reason", "別")
    assert run("unobject", name, "F1") == EXIT_OK
    assert selection_of(review)["objections"] == {"F2": "別"}
    assert run("unobject", name, "F1") == EXIT_PROCESSING_FAILURE
    assert "異議の無い指摘の番号です: F1" in capsys.readouterr().err
    assert run("unobject", name, "F99") == EXIT_PROCESSING_FAILURE
    assert "知らない指摘の番号です: F99" in capsys.readouterr().err
    assert selection_of(review)["objections"] == {"F2": "別"}


def test_stale_selection_is_shown(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    name = str(review.id)
    run("pick", name, "F1")
    findings_path = review.id.directory() / "findings.json"
    state = json.loads(findings_path.read_text("utf-8"))
    state["groups"]["micro"] = [finding(7)]
    state["themes"] = []
    findings_path.write_text(json.dumps(state), encoding="utf-8")
    capsys.readouterr()
    assert run("show", name) == EXIT_OK
    out = capsys.readouterr().out
    assert "古い選択" in out
    assert "F1" in out
    assert run("show", name, "--json") == EXIT_OK
    assert json.loads(capsys.readouterr().out)["selection"]["stale"] == ["F1"]


def test_show_overview_and_marks(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    name = str(review.id)
    run("pick", name, "F1")
    run("hold", name, "F2")
    run("object", name, "F3", "--reason", "x")
    capsys.readouterr()
    run("show", name)
    out = capsys.readouterr().out
    assert "最終的な指摘 1 件、保留 1 件、異議 1 件" in out
    run("show", name, "micro")
    out = capsys.readouterr().out
    assert "F1 [picked]" in out
    assert "F2 [held]" in out
    assert "F3 [異議]" in out
    run("show", name, "micro", "--json")
    assert json.loads(capsys.readouterr().out)["selection"]["picked"] == ["F1"]


def test_json_output_after_operation(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    run("pick", str(review.id), "F1", "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["picked"] == ["F1"]
    assert out["held"] == []
    assert out["objections"] == {}


def test_show_json_fills_missing_triage(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    path = review.id.directory() / "findings.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    macro = [finding(1), finding(2, "rejected")]
    for item in macro:
        item["group"] = "macro"
        item["triage"] = None
    state["groups"]["macro"] = macro
    path.write_text(json.dumps(state), encoding="utf-8")
    capsys.readouterr()
    assert run("show", str(review.id), "macro", "--json") == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert [f["triage"] for f in shown["findings"]] == ["must_read", "rejected"]


def test_upgrade_table_has_every_step() -> None:
    check_upgrades(SELECTION_FILE, SELECTION_FORMAT, _UPGRADES)


def test_each_operation_replaces_the_previous_verdict(review: Review) -> None:
    name = str(review.id)
    run("hold", name, "F1")
    run("pick", name, "F1")
    assert selection_of(review) == {"picked": ["F1"], "held": [], "objections": {}}
    run("object", name, "F1", "--reason", "誤り")
    assert selection_of(review) == {"picked": [], "held": [], "objections": {"F1": "誤り"}}
    run("hold", name, "F1")
    assert selection_of(review) == {"picked": [], "held": ["F1"], "objections": {}}
    run("pick", name, "F1")
    run("hold", name, "F1")
    assert selection_of(review) == {"picked": [], "held": ["F1"], "objections": {}}


def test_unhold_removes_only_held(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    name = str(review.id)
    run("hold", name, "F1")
    assert run("unhold", name, "F1") == EXIT_OK
    assert selection_of(review) == {"picked": [], "held": [], "objections": {}}
    capsys.readouterr()
    assert run("unhold", name, "F1") == EXIT_PROCESSING_FAILURE
    assert "held" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("setup", "undo"),
    [
        (("hold",), "unpick"),
        (("pick",), "unhold"),
        (("pick",), "unobject"),
        (("hold",), "unobject"),
        (("object",), "unpick"),
        (("object",), "unhold"),
    ],
)
def test_undo_with_a_different_verdict_fails_and_keeps_it(
    review: Review, capsys: pytest.CaptureFixture[str], setup: tuple[str], undo: str
) -> None:
    name = str(review.id)
    arguments = ["--reason", "誤り"] if setup[0] == "object" else []
    run(setup[0], name, "F1", *arguments)
    before = selection_of(review)
    capsys.readouterr()
    assert run(undo, name, "F1") == EXIT_PROCESSING_FAILURE
    assert "F1" in capsys.readouterr().err
    assert selection_of(review) == before


def test_selection_v2_with_overlap_fails_to_read(review: Review) -> None:
    path = review.id.directory() / SELECTION_FILE
    path.write_text(
        json.dumps({"format": 2, "picked": ["F1"], "held": ["F1"], "objections": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ReviewError, match="F1 が複数の扱いに入っています"):
        load_selection(review.id.directory())


def test_json_keys_are_unchanged(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    run("pick", str(review.id), "F1", "--json")
    assert set(json.loads(capsys.readouterr().out)) >= {"picked", "held", "objections"}


def test_selection_subcommands_are_registered() -> None:
    from jigane.commands import build_parser  # noqa: PLC0415 試験の中で読む

    choices = next(
        action.choices
        for action in build_parser()._actions
        if hasattr(action, "choices") and action.choices and "pick" in action.choices
    )
    assert {"pick", "unpick", "hold", "unhold", "object", "unobject"} <= set(choices)


@pytest.mark.parametrize(
    ("undo", "setup_first", "setup_second"),
    [
        ("unpick", ("pick",), ("hold",)),
        ("unhold", ("hold",), ("pick",)),
        ("unobject", ("object", "--reason", "誤り"), ("pick",)),
    ],
)
def test_undo_by_theme_removes_only_matching_members(
    review: Review,
    capsys: pytest.CaptureFixture[str],
    undo: str,
    setup_first: tuple[str, ...],
    setup_second: tuple[str, ...],
) -> None:
    name = str(review.id)
    run(setup_first[0], name, "F1", *setup_first[1:])
    run(setup_second[0], name, "F2", *setup_second[1:])
    capsys.readouterr()
    assert run(undo, name, "B1") == EXIT_OK
    captured = capsys.readouterr()
    assert "外した指摘: F1" in captured.err
    assert "扱いが違うため外さなかった指摘: F2" in captured.err
    assert "F1" not in load_selection(review.id.directory()).verdicts
    assert "F2" in load_selection(review.id.directory()).verdicts


def test_undo_by_theme_json_keeps_stdout_shape(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    name = str(review.id)
    run("pick", name, "F1")
    run("hold", name, "F2")
    capsys.readouterr()
    assert run("unpick", name, "B1", "--json") == EXIT_OK
    captured = capsys.readouterr()
    assert set(json.loads(captured.out)) >= {"picked", "held", "objections"}
    assert "扱いが違うため外さなかった指摘: F2" in captured.err


def test_undo_by_theme_without_any_match_fails(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    name = str(review.id)
    run("hold", name, "F1", "F2")
    before = selection_of(review)
    capsys.readouterr()
    assert run("unpick", name, "B1") == EXIT_PROCESSING_FAILURE
    assert "F1, F2" in capsys.readouterr().err
    assert selection_of(review) == before


def test_undo_by_finding_number_stays_strict(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    name = str(review.id)
    run("pick", name, "F1")
    run("hold", name, "F2")
    before = selection_of(review)
    capsys.readouterr()
    assert run("unpick", name, "F1", "F2") == EXIT_PROCESSING_FAILURE
    assert "F2" in capsys.readouterr().err
    assert selection_of(review) == before


@pytest.mark.parametrize("replace", ["pick", "hold"])
@pytest.mark.parametrize("flags", [[], ["--json"]])
def test_replacing_an_objection_prints_the_lost_reason(
    review: Review, capsys: pytest.CaptureFixture[str], replace: str, flags: list[str]
) -> None:
    name = str(review.id)
    run("object", name, "F1", "--reason", "これは誤りです")
    capsys.readouterr()
    assert run(replace, name, "F1", *flags) == EXIT_OK
    captured = capsys.readouterr()
    assert "消えた理由: これは誤りです" in captured.err
    assert len(captured.err.strip().splitlines()) == 1
    if flags:
        assert set(json.loads(captured.out)) >= {"picked", "held", "objections"}


def test_replacing_a_non_objection_prints_nothing(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    name = str(review.id)
    run("pick", name, "F1")
    run("hold", name, "F1")
    run("pick", name, "F1")
    assert "消えた理由" not in capsys.readouterr().err
