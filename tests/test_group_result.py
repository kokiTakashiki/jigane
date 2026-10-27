import json
from pathlib import Path
from typing import Any

import pytest
from fakes import as_run
from group_state_support import seed_group

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.groups.definitions import MACRO, MICRO, SECURITY, GroupId
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills.group_result import (
    Failed,
    HasFindings,
    NoFindings,
    NotRun,
    group_outcome,
)


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "home"))
    return create_feature_review(root, ["src"], "p")


def _show(capsys: pytest.CaptureFixture[str], *arguments: str) -> str:
    assert main(["show", "shop:p", *arguments]) == EXIT_OK
    return capsys.readouterr().out


def _show_json(capsys: pytest.CaptureFixture[str], group: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(_show(capsys, group, "--json"))
    return document


def test_outcome_is_decided_from_run_state_and_finding_count() -> None:
    ran: dict[str, Any] = {"shards": 2, "succeeded_shards": 2}
    assert group_outcome(as_run(None), 0) == NotRun()
    assert group_outcome(as_run(ran), 0) == NoFindings(0, None, 0)
    assert group_outcome(as_run({**ran, "discovered": 3, "discarded": []}), 0) == NoFindings(3, 0)
    assert group_outcome(as_run(ran), 1) == HasFindings()
    partial = group_outcome(as_run({"shards": 2, "succeeded_shards": 1}), 0)
    assert partial == Failed("2 シャードのうち 1 個の発見役が失敗しました")
    assert isinstance(group_outcome(as_run({"failed_splits": 2}), 0), Failed)
    assert isinstance(group_outcome(as_run({"budget_stops": [{}]}), 0), Failed)
    # 指摘が残った実行の一部の失敗は、指摘の一覧が状態を言う
    assert group_outcome(as_run({"shards": 2, "succeeded_shards": 1}), 3) == HasFindings()


@pytest.mark.parametrize("group", [MICRO, SECURITY])
def test_show_distinguishes_four_states(
    review: Review, capsys: pytest.CaptureFixture[str], group: GroupId
) -> None:
    not_run = _show_json(capsys, group)
    assert not_run["result"] == "not_run"
    assert "未実行" in _show(capsys, group)

    seed_group(review, group, [], {"discovered": 4, "dropped_without_evidence": 1})
    clean = _show_json(capsys, group)
    assert clean["result"] == "no_findings"
    assert (clean["discovered"], clean["discarded"], clean["dropped"]) == (4, None, 1)
    assert clean["findings"] == []
    assert clean["run"] is not None
    text = _show(capsys, group)
    assert "指摘なし（発見役の候補 4 件は検証を経て残りませんでした" in text

    seed_group(review, group, [], {"shards": 3, "succeeded_shards": 1})
    failed = _show_json(capsys, group)
    assert failed["result"] == "failed"
    assert failed["failure_reason"] == "3 シャードのうち 2 個の発見役が失敗しました"
    assert "実行は失敗しました: 3 シャードのうち 2 個の発見役が失敗しました" in _show(
        capsys, group
    )


def test_macro_no_findings_carries_considered_counts(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_group(review, MACRO, [], {"discovered": 3, "discarded": []})
    body = _show_json(capsys, "macro")
    assert body["result"] == "no_findings"
    assert (body["discovered"], body["discarded"]) == (3, 0)
    assert "指摘なし（検討 3 件）" in _show(capsys, "macro")


def test_show_json_carries_the_band_text(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    def texts(group: dict[str, Any]) -> tuple[str, str, str]:
        return (group["result_title"], group["result_note"], group["result_note_without_count"])

    assert texts(_show_json(capsys, "micro")) == (
        "未実行",
        "（まだ走らせていません）",
        "（まだ走らせていません）",
    )
    seed_group(review, MACRO, [], {"discovered": 3, "discarded": []})
    assert texts(_show_json(capsys, "macro")) == (
        "指摘なし",
        "（検討 3 件）",
        "",
    )
    seed_group(review, SECURITY, [], {"shards": 3, "succeeded_shards": 1})
    reason = (
        "実行は失敗しました: 3 シャードのうち 2 個の発見役が失敗しました。"
        "指摘が無かったとは言えません"
    )
    assert texts(_show_json(capsys, "security")) == (reason, "", "")
