import csv
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFake, as_run, discovered, settings_for
from review_fakes import group_result, history_rounds

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings.discard import (
    NO_CONCERN_CLAIM,
    Considered,
    DiscardedCandidate,
    DiscardState,
    DiscardSummary,
    discard_summary,
    summary_of_counts,
)
from jigane.findings.discarded_nits import LEGACY_NOT_SELECTABLE
from jigane.findings.records import (
    HISTORY_FILE,
    GroupResult,
    ReplacedRun,
    merge_group,
    triaged_dicts,
)
from jigane.groups.definitions import MACRO
from jigane.publishing.rendering import build_context
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.reviews.selection import Picked, current_findings, load_selection, save_selection
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput

DISCARDED = [
    {"source": "S1", "reason": "図に現れていない呼び出しに頼る"},
    {"source": "S2", "reason": "保存されない内部の型である"},
]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _fake_with_discarded(findings: list[dict[str, Any]]) -> MacroFake:
    output = {
        "findings": [{**f, "in_scope": True, "scope_reason": ""} for f in findings],
        "merged": [],
        "discarded": DISCARDED,
    }
    return MacroFake([], output=output)


def _run(review: Review, fake: MacroFake) -> macro.MacroRun:
    return macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))


def test_discarded_is_saved_in_runs_json(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    result = _run(review, _fake_with_discarded([discovered("M2")]))
    assert result.run_state.to_json()["discarded"] == DISCARDED
    assert result.run_state.discovered == 3
    runs = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))
    assert runs["macro"]["discarded"] == DISCARDED
    assert as_run(runs["macro"]).considered == Considered(
        3,
        (
            DiscardedCandidate("S1", "図に現れていない呼び出しに頼る"),
            DiscardedCandidate("S2", "保存されない内部の型である"),
        ),
    )


def test_old_runs_json_without_discarded_is_readable(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _run(review, MacroFake([discovered("M2")]))
    runs_path = review.id.directory() / "runs.json"
    runs = json.loads(runs_path.read_text("utf-8"))
    del runs["macro"]["discarded"]
    runs_path.write_text(json.dumps(runs), encoding="utf-8")
    assert as_run(runs["macro"]).considered is None
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    assert "検討" not in capsys.readouterr().out
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["run"]["considered"] is None


def test_show_macro_shows_discarded_candidates_as_plain_nits(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    fake = _fake_with_discarded([discovered("M2")])
    assert main(["run", "shop:p", "macro"], invoke=fake) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    text = capsys.readouterr().out
    # 捨てた判断は見せない。見出し、内訳、観点の値、捨てた理由の呼び名が出ない
    for hidden in ("捨てた", "discarded", "前の回で直った", "nit として示す", "残した"):
        assert hidden not in text
    assert "検討 3 件" in text
    # 懸念の文が無い nit は、捨てた理由の文で代えず、決まった一文で出る
    assert "図に現れていない呼び出しに頼る" not in text
    assert "保存されない内部の型である" not in text
    assert text.count(f" nit  {NO_CONCERN_CLAIM}") == 2
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    assert shown["run"]["considered"] == {"discovered": 3, "discarded": DISCARDED}
    nits = [f for f in shown["findings"] if f["viewpoint"] == "discarded"]
    assert [f["claim"] for f in nits] == [NO_CONCERN_CLAIM] * 2
    assert all("label" not in f["nit_reason"] for f in nits)


def test_show_macro_with_settled_candidate_keeps_the_judgment_hidden(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    settled = "前の回 F1 は、型の追加で直った。"
    output = {
        "findings": [{**discovered("M2"), "in_scope": True, "scope_reason": ""}],
        "merged": [],
        "discarded": [*DISCARDED, {"source": "S3", "reason": settled}],
    }
    assert main(["run", "shop:p", "macro"], invoke=MacroFake([], output=output)) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    text = capsys.readouterr().out
    assert "捨てた" not in text
    assert "前の回で直った" not in text
    assert "型の追加で直った" not in text
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    considered = json.loads(capsys.readouterr().out)["run"]["considered"]
    assert "not_nit" not in considered


def test_discarded_candidates_are_saved_as_nit_findings(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    result = _run(review, _fake_with_discarded([discovered("M2")]))
    nits = [f for f in triaged_dicts(result.findings) if f["viewpoint"] == "discarded"]
    # 主張は懸念の文で、取り出せなければ理由の文のまま。添え文は主張に含めず、nit_reason に分ける
    assert [f["claim"] for f in nits] == [
        "図に現れていない呼び出しに頼る",
        "保存されない内部の型である",
    ]
    assert [f["nit_reason"] for f in nits] == [
        {
            "kind": "unverifiable_from_diagram",
            "text": "図に現れていない呼び出しに頼る",
            "concern": None,
            "places": [],
        },
        {
            "kind": "not_a_contract",
            "text": "保存されない内部の型である",
            "concern": None,
            "places": [],
        },
    ]
    # 種類と懸念の文は nit_reason だけが持つ
    assert all("discard_kind" not in f and "concern" not in f for f in nits)
    for finding in nits:
        assert (finding["severity"], finding["body"], finding["evidence"]) == ("nit", "", [])
        assert finding["triage"] == "remaining"
    # 通常の指摘と同じ番号の流れで振る
    assert [f["id"] for f in triaged_dicts(result.findings)] == ["F1", "F2", "F3"]
    saved = json.loads((review.id.directory() / "findings.json").read_text("utf-8"))
    assert [f["nit_reason"]["kind"] for f in saved["groups"]["macro"] if "nit_reason" in f] == [
        "unverifiable_from_diagram",
        "not_a_contract",
    ]


def test_discarded_nits_do_not_count_as_must_read(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    fake = _fake_with_discarded([discovered("M2")])
    assert main(["run", "shop:p", "macro"], invoke=fake) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    shown = json.loads(capsys.readouterr().out)
    by_triage = [f["triage"] for f in shown["findings"]]
    assert sorted(by_triage) == ["must_read", "remaining", "remaining"]
    assert [f["severity"] for f in shown["findings"] if f["triage"] == "must_read"] == ["warning"]


def test_discarded_nits_stay_out_of_the_next_round_history(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _run(review, _fake_with_discarded([discovered("M2")]))
    text = macro.history_text(AssemblyInput.load(review))
    assert "discarded" not in text
    assert "図に現れていない呼び出しに頼る" not in text
    assert "前の回 F1 M2 warning" in text


def test_round_of_only_discarded_nits_reads_as_no_findings(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    _run(review, _fake_with_discarded([]))
    assert macro.history_text(AssemblyInput.load(review)) == "- 前の回: 指摘なし"


def test_show_macro_with_nothing_considered(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    assert main(["run", "shop:p", "macro"], invoke=MacroFake([])) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    text = capsys.readouterr().out
    assert "検討した候補はありません" in text
    assert "[捨てた候補]" not in text


def test_discard_summary_separates_the_three_states() -> None:
    assert discard_summary(None) == DiscardSummary(DiscardState.UNRECORDED)
    assert discard_summary(Considered(0, ())) == DiscardSummary(DiscardState.NONE_DISCOVERED)
    measured = discard_summary(Considered(4, (DiscardedCandidate("S1", "r"),)))
    assert measured == DiscardSummary(DiscardState.MEASURED, 4, 1, 0.25)


def test_discard_summary_carries_numbers_only_when_measured() -> None:
    with pytest.raises(ValueError, match="MEASURED"):
        DiscardSummary(DiscardState.UNRECORDED, 1, 0, 0.0)
    with pytest.raises(ValueError, match="MEASURED"):
        DiscardSummary(DiscardState.MEASURED)


def _usage_total(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main(["usage", "shop:p", "--json"]) == EXIT_OK
    rows: list[dict[str, Any]] = json.loads(capsys.readouterr().out)["rows"]
    return rows[-1]


def _macro_run_with(project: Path, edit: Callable[[dict[str, Any]], None]) -> Review:
    review = create_feature_review(project, ["src"], "p")
    _run(review, _fake_with_discarded([discovered("M2")]))
    runs_path = review.id.directory() / "runs.json"
    runs = json.loads(runs_path.read_text("utf-8"))
    edit(runs["macro"])
    runs_path.write_text(json.dumps(runs), encoding="utf-8")
    return review


def _csv_total(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    assert main(["usage", "shop:p", "--csv"]) == EXIT_OK
    header, *rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    return dict(zip(header, rows[-1], strict=True))


def _table_total(capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert main(["usage", "shop:p"]) == EXIT_OK
    return capsys.readouterr().out.splitlines()[-1].split()


def _set_discovered_zero(state: dict[str, Any]) -> None:
    state["discovered"] = 0
    state["discarded"] = []


def _drop_discarded(state: dict[str, Any]) -> None:
    del state["discarded"]


def test_usage_shows_measured_discard_ratio(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _macro_run_with(project, lambda _state: None)
    total = _usage_total(capsys)
    assert total["discard_state"] == "measured"
    assert (total["discovered"], total["discarded"], total["discard_ratio"]) == (3, 2, 0.667)
    csv_total = _csv_total(capsys)
    assert (csv_total["discard_state"], csv_total["discard_ratio"]) == ("measured", "0.667")
    assert main(["usage", "shop:p"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[-3:] == ["発見", "捨てた", "捨てた割合"]
    assert lines[-1].split()[-3:] == ["3", "2", "67%"]


def test_usage_shows_none_discovered_apart_from_unrecorded(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _macro_run_with(project, _set_discovered_zero)
    total = _usage_total(capsys)
    assert total["discard_state"] == "none_discovered"
    assert (total["discovered"], total["discarded"], total["discard_ratio"]) == (None, None, None)
    csv_total = _csv_total(capsys)
    assert csv_total["discard_state"] == "none_discovered"
    assert (csv_total["discovered"], csv_total["discard_ratio"]) == ("", "")
    assert _table_total(capsys)[-1] == "発見なし"


def test_usage_shows_unrecorded_for_old_runs_json(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _macro_run_with(project, _drop_discarded)
    total = _usage_total(capsys)
    assert total["discard_state"] == "unrecorded"
    assert (total["discovered"], total["discarded"], total["discard_ratio"]) == (None, None, None)
    csv_total = _csv_total(capsys)
    assert csv_total["discard_state"] == "unrecorded"
    assert (csv_total["discovered"], csv_total["discard_ratio"]) == ("", "")
    assert _table_total(capsys)[-1] == "不明"


def test_each_replaced_run_leaves_its_discard_counts_in_the_history(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    _run(review, _fake_with_discarded([discovered("M2")]))
    _run(review, MacroFake([discovered("M2")]))
    _run(review, MacroFake([]))
    rounds = history_rounds(review.id.directory(), MACRO)
    assert [r.discard for r in reversed(rounds)] == [
        DiscardSummary(DiscardState.MEASURED, 3, 2, 2 / 3),
        DiscardSummary(DiscardState.MEASURED, 1, 0, 0.0),
    ]
    assert main(["usage", "shop:p", "--json"]) == EXIT_OK
    entries = json.loads(capsys.readouterr().out)["rows"][-1]["discard_rounds"]
    assert [(e["discard_state"], e["discovered"], e["discarded"]) for e in entries] == [
        ("measured", 3, 2),
        ("measured", 1, 0),
        ("none_discovered", None, None),
    ]
    assert main(["usage", "shop:p"]) == EXIT_OK
    text = capsys.readouterr().out
    assert "方針の回ごとの捨てた割合（古い回から）" in text
    assert "67%（発見 3、捨てた 2）" in text
    assert "発見なし" in text


def _finding() -> dict[str, Any]:
    return {"id": "F1", "triage": "must_read"}


def test_history_rounds_without_counts_are_read_as_unrecorded(tmp_path: Path) -> None:
    (tmp_path / HISTORY_FILE).write_text(
        json.dumps({"groups": {"macro": [{"ran_at": "t", "findings": []}]}}), encoding="utf-8"
    )
    (round_,) = history_rounds(tmp_path, MACRO)
    assert round_.discard == DiscardSummary(DiscardState.UNRECORDED)
    merge_group(tmp_path, MACRO, group_result([_finding()]), None)
    merge_group(tmp_path, MACRO, GroupResult(()), ReplacedRun("u", summary_of_counts(2, 0)))
    assert [r.discard.state for r in history_rounds(tmp_path, MACRO)] == [
        DiscardState.MEASURED,
        DiscardState.UNRECORDED,
    ]


def test_macro_findings_drop_from_concern_with_a_warning(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    review = create_feature_review(project, ["src"], "p")
    claimed = {**discovered("M2"), "from_concern": "F1"}
    result = _run(review, _fake_with_discarded([claimed]))
    assert all(finding.finding.from_concern is None for finding in result.findings)
    assert "from_concern" in caplog.text


def _as_old_record(review: Review) -> None:
    """写した nit を指摘から外し、runs.json の considered だけが捨てた候補を持つ古い記録にする。"""
    path = review.id.directory() / "findings.json"
    state = json.loads(path.read_text("utf-8"))
    state["groups"]["macro"] = [
        f for f in state["groups"]["macro"] if f["viewpoint"] != "discarded"
    ]
    path.write_text(json.dumps(state), encoding="utf-8")


def test_old_record_shows_discarded_candidates_as_nit_lines_without_the_reason(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = _fake_with_discarded([discovered("M2")])
    assert main(["run", "shop:p", "macro"], invoke=fake) == EXIT_OK
    _as_old_record(review)
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    text = capsys.readouterr().out
    assert text.count(f" nit  {NO_CONCERN_CLAIM}") == 2
    assert "図に現れていない呼び出しに頼る" not in text
    # nit の行が並ぶ結果を「指摘なし」と読ませず、捨てた件数も出さない
    assert "指摘なし" not in text
    assert "捨てた" not in text
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    nits = [
        f for f in json.loads(capsys.readouterr().out)["findings"] if f["viewpoint"] == "discarded"
    ]
    assert [f["id"] for f in nits] == ["D-S1", "D-S2"]
    assert [f["claim"] for f in nits] == [NO_CONCERN_CLAIM] * 2
    assert all(f["severity"] == "nit" and f["in_reply"] is False for f in nits)
    assert all(f["selection_note"] == LEGACY_NOT_SELECTABLE for f in nits)
    # 保存物は書き換えない
    stored = json.loads((review.id.directory() / "findings.json").read_text("utf-8"))
    assert all(f["viewpoint"] != "discarded" for f in stored["groups"]["macro"])


def _old_record_review(project: Path) -> Review:
    review = create_feature_review(project, ["src"], "p")
    assert (
        main(["run", "shop:p", "macro"], invoke=_fake_with_discarded([discovered("M2")]))
        == EXIT_OK
    )
    _as_old_record(review)
    return review


def test_old_record_nit_lines_carry_the_not_selectable_mark(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _old_record_review(project)
    capsys.readouterr()
    assert main(["show", "shop:p", "macro"]) == EXIT_OK
    lines = [x for x in capsys.readouterr().out.splitlines() if "D-S" in x]
    assert len(lines) == 2
    assert all(line.endswith(f"[{LEGACY_NOT_SELECTABLE}]") for line in lines)


def test_other_findings_have_a_null_selection_note(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_feature_review(project, ["src"], "p")
    assert (
        main(["run", "shop:p", "macro"], invoke=_fake_with_discarded([discovered("M2")]))
        == EXIT_OK
    )
    capsys.readouterr()
    assert main(["show", "shop:p", "macro", "--json"]) == EXIT_OK
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert findings
    assert all(f["selection_note"] is None and f["in_reply"] is True for f in findings)


@pytest.mark.parametrize("command", ["pick", "unpick", "hold"])
def test_selecting_an_old_record_nit_fails_with_the_not_selectable_message(
    project: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    _old_record_review(project)
    capsys.readouterr()
    assert main([command, "shop:p", "D-S1"]) != EXIT_OK
    err = capsys.readouterr().err
    assert LEGACY_NOT_SELECTABLE in err
    assert "知らない指摘の番号です" not in err


def test_report_context_leaves_out_old_record_nits(project: Path) -> None:
    review = _old_record_review(project)
    directory = review.id.directory()
    kept = [f.finding.id for f in current_findings(directory).values()]
    assert kept
    selection = load_selection(directory)
    selection.verdicts = {n: Picked() for n in kept}
    save_selection(directory, selection)
    context, _ = build_context(review)
    assert [f["number"] for f in context["findings"]] == sorted(kept)
    assert not any(f["number"].startswith("D-") for f in context["findings"])


def test_mixed_legacy_and_unknown_numbers_report_both(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _old_record_review(project)
    capsys.readouterr()
    assert main(["pick", "shop:p", "D-S1", "F99"]) != EXIT_OK
    err = capsys.readouterr().err
    assert f"{LEGACY_NOT_SELECTABLE}: D-S1。知らない指摘の番号です: F99" in err


def test_unobject_an_old_record_nit_fails_with_the_not_selectable_message(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _old_record_review(project)
    capsys.readouterr()
    assert main(["unobject", "shop:p", "D-S1"]) != EXIT_OK
    err = capsys.readouterr().err
    assert LEGACY_NOT_SELECTABLE in err
    assert "知らない指摘の番号です" not in err


def test_object_an_old_record_nit_fails_with_the_not_selectable_message(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _old_record_review(project)
    capsys.readouterr()
    assert main(["object", "shop:p", "D-S1", "--reason", "x"]) != EXIT_OK
    err = capsys.readouterr().err
    assert LEGACY_NOT_SELECTABLE in err
    assert "知らない指摘の番号です" not in err
