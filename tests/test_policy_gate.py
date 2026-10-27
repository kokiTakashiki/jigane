import json
from pathlib import Path
from typing import Any

import pytest
from fakes import MacroFake, MicroFake, clear_policy, d_finding, discovered, settings_for
from group_state_support import rewrite_raw_findings
from test_security import SecurityFake, s_finding, write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings.records import Triage, load_findings_state, mark_stale, read_stored_state
from jigane.groups.definitions import MACRO, GroupId
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput
from jigane.skills.order import policy_notice


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def run_macro(review: Review, fake: MacroFake) -> None:
    macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))


def test_notice_when_macro_never_ran(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    notice = policy_notice(review)
    assert notice is not None
    assert "実行されていません" in notice


def test_notice_when_macro_is_stale(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    assert policy_notice(review) is None
    mark_stale(review.id.directory())
    notice = policy_notice(review)
    assert notice is not None
    assert "古くなっています" in notice


def test_notice_on_must_read_and_lists_ids(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run_macro(review, MacroFake([discovered("M2")]))
    notice = policy_notice(review)
    assert notice is not None
    assert "F1" in notice


def test_no_notice_without_must_read(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run_macro(review, MacroFake([{**discovered("M2"), "severity": "nit"}]))
    assert policy_notice(review) is None


def test_run_micro_runs_with_notice_when_macro_never_ran(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({1: [d_finding("D1")]})
    assert main(["run", str(review.id), "micro", "--json"], invoke=fake) == EXIT_OK
    captured = capsys.readouterr()
    assert fake.calls != []
    assert "実行されていません" in json.loads(captured.out)["policy_notice"]
    assert "注: 方針のレビュー: 方針のレビューがまだ実行されていません" in captured.err
    assert captured.err.strip().endswith("実装のレビューを続けます")


def test_run_micro_runs_with_notice_when_macro_is_stale(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    mark_stale(review.id.directory())
    fake = MicroFake({1: [d_finding("D1")]})
    assert main(["run", str(review.id), "micro"], invoke=fake) == EXIT_OK
    assert "古くなっています" in capsys.readouterr().err
    assert fake.calls != []


def test_run_micro_runs_with_notice_when_must_read_remains(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    run_macro(review, MacroFake([discovered("M2")]))
    fake = MicroFake({1: [d_finding("D1")]})
    assert main(["run", str(review.id), "micro", "--json"], invoke=fake) == EXIT_OK
    captured = capsys.readouterr()
    assert "F1" in json.loads(captured.out)["policy_notice"]
    assert "F1" in captured.err
    assert fake.calls != []


def test_run_micro_has_no_notice_without_must_read(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    fake = MicroFake({1: [d_finding("D1")]})
    assert main(["run", str(review.id), "micro", "--json"], invoke=fake) == EXIT_OK
    captured = capsys.readouterr()
    assert json.loads(captured.out)["policy_notice"] is None
    assert "注: 方針のレビュー" not in captured.err


def test_run_security_runs_with_notice(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp()
    fake = SecurityFake({1: [s_finding()]})
    assert main(["run", str(review.id), "security"], invoke=fake) == EXIT_OK
    err = capsys.readouterr().err
    assert "注: 方針のレビュー:" in err
    assert err.strip().endswith("セキュリティのレビューを続けます")


def test_estimate_carries_notice(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    assert main(["estimate", str(review.id), "micro", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert "実行されていません" in data["policy_notice"]
    assert "blocked" not in data
    assert main(["estimate", str(review.id), "micro"]) == EXIT_OK
    assert "注: 方針のレビュー: 方針のレビューがまだ実行されていません" in capsys.readouterr().out
    clear_policy(review)
    assert main(["estimate", str(review.id), "micro", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["policy_notice"] is None


def test_old_deferred_findings_load_as_remaining(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)

    def edit(raw: dict[str, Any]) -> None:
        raw["groups"]["micro"] = [{"id": "F1", "triage": "deferred"}]
        raw["themes"] = [{"id": "B1", "triage": "deferred", "finding_ids": ["F1"]}]

    rewrite_raw_findings(review.id.directory(), edit)
    loaded = load_findings_state(review.id.directory())
    assert loaded.groups[GroupId("micro")][0].decision.triage == "remaining"
    assert loaded.themes[0].triage == "remaining"


@pytest.mark.parametrize(
    ("severity", "noticed"),
    [("warning", True), ("nit", False)],
)
def test_notice_follows_the_triage_filled_for_an_unreadable_name(
    project: Path, severity: str, *, noticed: bool
) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    directory = review.id.directory()
    rewrite_raw_findings(
        directory,
        lambda raw: raw["groups"].__setitem__(
            "macro", [{"id": "F9", "triage": "bogus", "severity": severity, "adjudication": None}]
        ),
    )
    result = policy_notice(review)
    if noticed:
        assert result is not None
        assert "F9" in result
    else:
        assert result is None


def test_old_findings_without_triage_give_the_notice_and_show_the_same_triage(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    run_macro(review, MacroFake([discovered("M2"), {**discovered("M3"), "severity": "nit"}]))

    def edit(raw: dict[str, Any]) -> None:
        for finding in raw["groups"]["macro"]:
            del finding["triage"]

    rewrite_raw_findings(review.id.directory(), edit)
    # 区分の無い古い findings.json でも、読み込みで補われた必読が案内に出る
    notice = policy_notice(review)
    assert notice is not None
    assert "F1" in notice
    assert "F2" not in notice
    assert main(["show", str(review.id), "macro", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert [(f["id"], f["triage"]) for f in data["findings"]] == [
        ("F1", "must_read"),
        ("F2", "remaining"),
    ]


def out_of_scope_fake() -> MacroFake:
    final = {**discovered("M2"), "severity": "critical", "in_scope": False, "scope_reason": "r"}
    return MacroFake(
        [discovered("M2")], output={"findings": [final], "merged": [], "discarded": []}
    )


def test_no_notice_with_only_out_of_scope(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    run_macro(review, out_of_scope_fake())
    state = read_stored_state(review.id.directory())
    assert [f.triage for f in state.groups[MACRO]] == [Triage.OUT_OF_SCOPE]
    assert policy_notice(review) is None


def test_old_findings_without_out_of_scope_still_load(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    rewrite_raw_findings(
        review.id.directory(),
        lambda raw: raw["groups"].__setitem__(
            "macro", [{"id": "F1", "severity": "warning", "adjudication": None}]
        ),
    )
    loaded = load_findings_state(review.id.directory())
    assert loaded.groups[GroupId("macro")][0].decision.triage == "must_read"


def test_show_lists_out_of_scope_apart(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    run_macro(review, out_of_scope_fake())
    capsys.readouterr()
    assert main(["show", str(review.id), "macro"]) == EXIT_OK
    text = capsys.readouterr().out
    assert "<範囲外>" in text
    assert "<必読>" not in text
