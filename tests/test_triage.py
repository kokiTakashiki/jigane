import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import RECORD, MacroFake, MicroFake, clear_policy, d_finding, discovered, settings_for
from pydantic import ValidationError
from review_fakes import stored_finding
from triage_support import assigned, decided, stored

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings.records import (
    FoldedDecision,
    MustReadDecision,
    OutOfScopeDecision,
    RegularBody,
    RejectedDecision,
    RemainingDecision,
    ScopeBody,
    StoredFinding,
    StoredTheme,
    ToolBasis,
    Triage,
    TriageReason,
    UnknownTriageReason,
    decide_triage,
    fill_triage,
    is_rejected,
    restored_decision,
    theme_triage,
    triage_reason_of,
    triaged,
    triaged_dicts,
)
from jigane.groups.definitions import MACRO, MICRO, SECURITY, GroupId
from jigane.llm_runs import claude
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro, micro, triage
from jigane.skills.assembly import AssemblyInput
from jigane.skills.output_models import ThemesOutput

UNVERIFIED = TriageReason.WEAKENED_UNVERIFIED
TOOL_GUESS = TriageReason.TOOL_GUESS_UNFOUNDED


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n" + "x = 1\n" * 3500, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def with_severity(
    finding: dict[str, Any], severity: str, claim: str | None = None
) -> dict[str, Any]:
    return {**finding, "severity": severity, "claim": claim or finding["claim"]}


def micro_findings(review: Review, items: list[dict[str, Any]], fake: MicroFake) -> list[Any]:
    clear_policy(review)
    fake.by_shard = {1: items}
    run = micro.run_micro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    return triaged_dicts(run.findings)


def saved(review: Review) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(
        (review.id.directory() / "findings.json").read_text("utf-8")
    )
    return state


def runs(review: Review) -> dict[str, Any]:
    state: dict[str, Any] = json.loads((review.id.directory() / "runs.json").read_text("utf-8"))
    return state


SCOPE_RECORD = {"paths": ["x"], "reason": "r"}


def test_macro_triage(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MacroFake(
        [
            with_severity(discovered("M2"), "critical"),
            with_severity(discovered("M3"), "warning"),
            with_severity(discovered("M4"), "nit"),
        ]
    )
    result = macro.run_macro(AssemblyInput.load(review), settings_for(fake, max_budget_usd=0.5))
    assert {f["viewpoint"]: f["triage"] for f in triaged_dicts(result.findings)} == {
        "M2": "must_read",
        "M3": "must_read",
        "M4": "remaining",
    }


def test_macro_triage_without_adjudication() -> None:
    nit = {"severity": "nit", "adjudication": None}
    warning = {"severity": "warning", "adjudication": None}
    assert assigned(MACRO, nit) is Triage.REMAINING
    assert assigned(MACRO, warning) is Triage.MUST_READ
    assert assigned(SECURITY, nit) is Triage.REMAINING
    assert assigned(MICRO, nit) is Triage.FOLDED
    assert assigned(MICRO, warning) is Triage.MUST_READ
    assert assigned(MICRO, warning, theme_position=3) is Triage.REMAINING
    assert assigned(MACRO, warning, scope_record=SCOPE_RECORD) is Triage.OUT_OF_SCOPE
    rejected = {
        "severity": "critical",
        "adjudication": {
            "kind": "rebuttal",
            "challenge": "c",
            "recheck": [],
            "reason": "r",
            "verdict": "rejected",
        },
    }
    assert assigned(MACRO, rejected, scope_record=SCOPE_RECORD) is Triage.REJECTED
    assert not is_rejected(stored(warning).body)


def test_fill_triage_differs_from_runtime_only_in_theme_position() -> None:
    # 補いはテーマの位置を再現できないので、実装の nit 以外を位置によらず必読にする
    late = {"id": "F1", "severity": "warning", "adjudication": None}
    runtime = assigned(MICRO, late, theme_position=5)
    filled = fill_triage(MICRO, stored_finding({**late, "triage": None}))
    assert (runtime, filled.decision.triage) == (Triage.REMAINING, Triage.MUST_READ)


def test_themes_ordering_numbers_and_top_three(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    items = [
        with_severity(d_finding("D1"), "warning", "w1"),  # F1
        with_severity(d_finding("D1"), "warning", "w2"),  # F2
        with_severity(d_finding("D1"), "warning", "w3"),  # F3
        with_severity(d_finding("D2"), "critical", "c4"),  # F4
        with_severity(d_finding("D2"), "warning", "w5"),  # F5
        with_severity(d_finding("D3"), "nit", "n6"),  # F6
        with_severity(d_finding("D3"), "nit", "n7"),  # F7
    ]
    fake = MicroFake({})
    fake.themes = [
        {"title": "A", "finding_ids": ["F1"]},
        {"title": "B", "finding_ids": ["F2", "F3"]},
        {"title": "C", "finding_ids": ["F4"]},
        {"title": "D", "finding_ids": ["F5", "F99", "F1"]},
    ]
    result = micro_findings(review, items, fake)
    by_id = {f["id"]: f for f in result}
    prompt = fake.theme_prompts[0]
    assert "n6" not in prompt
    state = saved(review)
    titles = [(t["id"], t["title"], t["triage"]) for t in state["themes"]]
    # C が critical で先頭、B は件数 2 で A より前、A と D は件数 1 で F 番号の小さい A が先
    assert titles == [
        ("B1", "C", "must_read"),
        ("B2", "B", "must_read"),
        ("B3", "A", "must_read"),
        ("B4", "D", "remaining"),
        ("B5", "nit", "folded"),
    ]
    assert by_id["F5"]["triage"] == "remaining"
    assert by_id["F5"]["theme"] == "B4"
    assert by_id["F1"]["theme"] == "B3"
    assert by_id["F6"]["triage"] == "folded"
    assert by_id["F7"]["theme"] == "B5"
    assert state["last_theme_number"] == 5
    bundling = runs(review)["micro"]["theme_bundling"]
    assert bundling == {
        "failed": False,
        "failure": None,
        "unknown_ids": 1,
        "duplicate_ids": 1,
        "unassigned": 0,
    }
    validator_errors = [f for f in result if not set(f) >= {"theme", "triage"}]
    assert validator_errors == []


def test_unassigned_findings_become_single_themes_and_numbers_continue(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({})
    fake.themes = [{"title": "only", "finding_ids": ["F1"]}]
    micro_findings(review, [d_finding("D1"), d_finding("D2")], fake)
    state = saved(review)
    assert [(t["id"], t["title"], t["finding_ids"]) for t in state["themes"]] == [
        ("B1", "only", ["F1"]),
        ("B2", "claim D2", ["F2"]),
    ]
    assert runs(review)["micro"]["theme_bundling"]["unassigned"] == 1
    fake.themes = []
    micro_findings(review, [d_finding("D1")], fake)
    state = saved(review)
    assert [t["id"] for t in state["themes"]] == ["B3"]
    assert state["last_theme_number"] == 3


def test_bundling_failure_makes_single_themes(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({})
    fake.themes = None
    result = micro_findings(review, [d_finding("D1"), d_finding("D2")], fake)
    assert [f["theme"] for f in result] == ["B1", "B2"]
    assert [f["triage"] for f in result] == ["must_read", "must_read"]
    bundling = runs(review)["micro"]["theme_bundling"]
    assert bundling["failed"] is True
    assert "boom" in bundling["failure"]
    assert runs(review)["micro"]["invocations"][-1]["role"] == "themes"


def test_bundling_invalid_output_is_failure(project: Path) -> None:
    class Bad:
        def __call__(self, _invocation: claude.Invocation) -> claude.InvocationResult:
            return claude.InvocationResult({"themes": "x"}, RECORD)

    findings = [
        stored(
            {
                "severity": "warning",
                "viewpoint": "D1",
                "claim": "c",
                "evidence": [],
                "adjudication": {
                    "kind": "rebuttal",
                    "challenge": "c",
                    "recheck": [],
                    "reason": "r",
                    "verdict": "kept",
                },
                "theme": None,
            }
        )
    ]
    outcome = triage.triage_micro(
        findings,
        0,
        working_directory=project,
        settings=settings_for(Bad(), max_budget_usd=1.0),
    )
    assert outcome.bundling is not None
    assert outcome.bundling["failed"] is True
    # 失敗の理由は、出力がスキーマに適合しなかったことを指す
    assert "スキーマに適合しません" in outcome.bundling["failure"]
    assert [t.id for t in outcome.theme_update.themes] == ["B1"]


def test_triage_micro_drops_a_stale_triage_reason_the_rule_did_not_set(project: Path) -> None:
    class Bad:
        def __call__(self, _invocation: claude.Invocation) -> claude.InvocationResult:
            return claude.InvocationResult({"themes": "x"}, RECORD)

    # 規則で下げていない指摘に古い triage_reason が残っていても、決定から書くので消える
    findings = [
        stored(
            {
                "severity": "warning",
                "viewpoint": "D1",
                "claim": "c",
                "evidence": [],
                "adjudication": {
                    "kind": "rebuttal",
                    "challenge": "c",
                    "recheck": [],
                    "reason": "r",
                    "verdict": "kept",
                },
                "theme": None,
                "triage_reason": "tool_guess_unfounded",
            }
        )
    ]
    outcome = triage.triage_micro(
        findings,
        0,
        working_directory=project,
        settings=settings_for(Bad(), max_budget_usd=1.0),
    )
    written = outcome.findings[0].to_json()
    assert "triage_reason" not in written
    assert written["triage"] == "must_read"
    assert written["evidence"] == []


def test_vet_themes_drops_empty_theme() -> None:
    findings = [stored_finding({"id": "F1"})]
    result = triage.vet_themes(
        [
            triage.RawTheme("x", ("F9",)),
            triage.RawTheme("y", ("F1",)),
            triage.RawTheme("z", ("F1",)),
        ],
        findings,
    )
    assert [(g.title, g.finding_ids) for g in result.groups] == [("y", ("F1",))]
    assert (result.unknown_ids, result.duplicate_ids, result.unassigned) == (1, 1, 0)


def test_only_nits_make_no_bundling_call(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    fake = MicroFake({})
    result = micro_findings(review, [with_severity(d_finding("D8"), "nit")], fake)
    assert fake.theme_prompts == []
    assert result[0]["triage"] == "folded"
    assert runs(review)["micro"]["theme_bundling"] is None


def test_show_forms(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    name = str(review.id)
    fake = MicroFake({1: [d_finding("D1"), with_severity(d_finding("D8"), "nit", "tiny")]})
    assert main(["run", name, "micro"], invoke=fake) == EXIT_OK
    capsys.readouterr()
    assert main(["show", name, "micro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[B1 claim D1] warning" in out
    assert "<nit> 1 件" in out
    assert "tiny" in out
    assert main(["show", name, "micro", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in data["themes"]] == ["B1", "B2"]


def test_show_macro_triage_order_and_json(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = create_feature_review(project, ["src"], "p")
    name = str(review.id)
    fake = MacroFake([with_severity(discovered("M4"), "nit"), discovered("M2")])
    assert main(["run", name, "macro"], invoke=fake) == EXIT_OK
    capsys.readouterr()
    assert main(["show", name, "macro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.index("<必読>") < out.index("F2 ") < out.index("<残り>") < out.index("F1 ")
    assert main(["show", name, "macro", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert [(f["id"], f["triage"]) for f in data["findings"]] == [
        ("F2", "must_read"),
        ("F1", "remaining"),
    ]


def _rebuttal(
    verdict: str,
    *,
    verified: bool = True,
    relies: bool = False,
    basis: str = "none",
    version: str = "",
) -> dict[str, Any]:
    return {
        "severity": "warning",
        "adjudication": {
            "kind": "rebuttal",
            "challenge": "c",
            "recheck": [],
            "verdict": verdict,
            "reason": "r",
            "verified": verified,
            "tool_claim": {"relies": relies, "basis": basis, "version": version},
        },
    }


@pytest.mark.parametrize("group", [MICRO, SECURITY])
def test_weakened_and_unverified_goes_remaining(group: GroupId) -> None:
    finding = _rebuttal("weakened", verified=False)
    decision = decided(group, finding, theme_position=0 if group == MICRO else None)
    assert (decision.triage, decision.reason) == (Triage.REMAINING, UNVERIFIED)


def test_weakened_but_verified_stays_must_read() -> None:
    decision = decided(MICRO, _rebuttal("weakened"), theme_position=0)
    assert (decision.triage, decision.reason) == (Triage.MUST_READ, None)


def test_kept_unverified_stays_must_read() -> None:
    finding = _rebuttal("kept", verified=False)
    assert assigned(MICRO, finding, theme_position=0) is Triage.MUST_READ


def test_tool_guess_without_basis_goes_remaining() -> None:
    for finding in (
        _rebuttal("kept", relies=True),
        _rebuttal("kept", relies=True, basis="docs", version=""),
    ):
        decision = decided(MICRO, finding, theme_position=0)
        assert (decision.triage, decision.reason) == (Triage.REMAINING, TOOL_GUESS)


@pytest.mark.parametrize("basis", ["docs", "run"])
def test_tool_guess_with_basis_stays_must_read(basis: str) -> None:
    finding = _rebuttal("kept", relies=True, basis=basis, version="just 1.40")
    assert assigned(MICRO, finding, theme_position=0) is Triage.MUST_READ


def test_old_adjudication_without_items_is_unchanged() -> None:
    old = {
        "severity": "warning",
        "adjudication": {
            "kind": "rebuttal",
            "challenge": "c",
            "recheck": [],
            "reason": "r",
            "verdict": "weakened",
        },
    }
    assert assigned(MICRO, old, theme_position=0) is Triage.MUST_READ


def test_triaged_records_reason() -> None:
    finding = triaged(MICRO, stored(_rebuttal("weakened", verified=False)), theme_position=0)
    assert (finding.decision.triage, finding.decision.reason) == (Triage.REMAINING, UNVERIFIED)
    other = triaged(MICRO, stored(_rebuttal("kept")), theme_position=0)
    assert "triage_reason" not in other.to_json()


def test_tool_basis_founded() -> None:
    assert [member.founded for member in ToolBasis] == [False, True, True]


def test_triage_reason_values_keep_the_stored_spelling() -> None:
    assert [reason.value for reason in TriageReason] == [
        "weakened_unverified",
        "tool_guess_unfounded",
    ]
    assert triage_reason_of("weakened_unverified") is TriageReason.WEAKENED_UNVERIFIED
    assert [triage_reason_of(value) for value in (None, "", 3)] == [None] * 3
    assert triage_reason_of("unknown") == UnknownTriageReason("unknown")


def _stored(reason: str) -> dict[str, Any]:
    return {
        "id": "F1",
        "severity": "warning",
        "viewpoint": "v",
        "claim": "c",
        "triage": "remaining",
        "triage_reason": reason,
    }


def test_stored_triage_reason_round_trips() -> None:
    finding = stored_finding(_stored("tool_guess_unfounded"))
    assert finding.decision == RemainingDecision(reason=TriageReason.TOOL_GUESS_UNFOUNDED)
    assert finding.to_json()["triage_reason"] == "tool_guess_unfounded"


def test_stored_unknown_triage_reason_survives_a_round_trip() -> None:
    stored = _stored("from_the_future")
    finding = stored_finding(stored)
    assert finding.triage is Triage.REMAINING
    assert finding.decision == RemainingDecision(reason=UnknownTriageReason("from_the_future"))
    assert finding.to_json()["triage_reason"] == "from_the_future"
    assert stored_finding(finding.to_json()).to_json() == finding.to_json()


def test_unknown_triage_reason_does_not_change_the_decision() -> None:
    finding = stored_finding(_stored("from_the_future"))
    decision = decide_triage(MICRO, finding, theme_position=0)
    assert (decision.triage, decision.reason) == (Triage.MUST_READ, None)


def _adjudicated(adjudication: dict[str, Any]) -> dict[str, Any]:
    return {"severity": "warning", "adjudication": adjudication}


def _full(kind: str, verdict: str, **extra: object) -> dict[str, Any]:
    """種類ごとの必須の欄を埋めた裁定。"""
    own = (
        {"challenge": "c", "recheck": []}
        if kind == "rebuttal"
        else {"alternative": "a", "defense": "d"}
    )
    return {"kind": kind, **own, "verdict": verdict, "reason": "r", **extra}


def _alternative(verdict: str, **extra: object) -> dict[str, Any]:
    return _adjudicated(_full("alternative", verdict, **extra))


@pytest.mark.parametrize("group", [MICRO, SECURITY])
def test_alternative_adjudication_is_never_downgraded(group: GroupId) -> None:
    # 代案の判定は規則の項目を持たないが、持っていても読まれない
    position = 0 if group == MICRO else None
    for finding in (
        _alternative("weakened"),
        _alternative("weakened", verified=False),
        _alternative("kept", tool_claim={"relies": True, "basis": "none", "version": ""}),
    ):
        decision = decided(group, finding, theme_position=position)
        assert (decision.triage, decision.reason) == (Triage.MUST_READ, None)


@pytest.mark.parametrize("kind", ["rebuttal", "alternative"])
def test_downgrade_depends_on_adjudication_kind(kind: str) -> None:
    finding = _adjudicated(_full(kind, "weakened", verified=False))
    decision = decided(MICRO, finding, theme_position=0)
    expected = (Triage.REMAINING, UNVERIFIED) if kind == "rebuttal" else (Triage.MUST_READ, None)
    assert (decision.triage, decision.reason) == expected


def _old_stored(severity: str, adjudication: dict[str, Any]) -> StoredFinding:
    return stored_finding(
        {
            "id": "F1",
            "severity": severity,
            "viewpoint": "v",
            "claim": "c",
            "triage": None,
            "theme": None,
            "adjudication": adjudication,
        }
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("rebuttal", Triage.REMAINING), ("alternative", Triage.MUST_READ)],
)
def test_fill_triage_applies_the_downgrade_rule_per_adjudication_kind(
    kind: str, expected: Triage
) -> None:
    # 補いは実行時と同じ規則を引くので、実装と安全性の指摘は記録の種類で下がるか決まる
    adjudication = _full(kind, "weakened", verified=False)
    for group in (MICRO, SECURITY):
        filled = fill_triage(group, _old_stored("warning", adjudication))
        assert filled.decision.triage is expected
        # 規則で下げたときは、実行時と同じく理由も残る
        assert filled.decision.reason is (UNVERIFIED if expected is Triage.REMAINING else None)


def test_downgraded_finding_round_trips_with_its_reason() -> None:
    adjudication = _full("rebuttal", "weakened", verified=False)
    downgraded = triaged(MICRO, _old_stored("warning", adjudication), theme_position=0)
    assert downgraded.decision.reason is UNVERIFIED
    restored = stored_finding(downgraded.to_json())
    assert restored.body == downgraded.finding
    assert restored.decision == RemainingDecision(reason=UNVERIFIED)
    kept = triaged(
        MICRO,
        _old_stored("warning", _full("alternative", "weakened", verified=False)),
        theme_position=0,
    )
    assert kept.decision.reason is None
    assert "triage_reason" not in kept.to_json()


def test_empty_deepens_is_read_as_not_having_it() -> None:
    finding = stored_finding({**_stored("tool_guess_unfounded"), "deepens": ""})
    assert isinstance(finding.body, RegularBody)
    assert "deepens" not in finding.to_json()


def test_scope_wins_over_deepening_and_keeps_deepens() -> None:
    scope = {"reason": "r"}
    finding = stored_finding({"id": "F2", "out_of_scope": scope, "deepens": "F1"})
    assert isinstance(finding.body, ScopeBody)
    assert finding.body.continued_from == "F1"
    assert finding.to_json()["deepens"] == "F1"
    empty = stored_finding({"id": "F3", "out_of_scope": scope, "deepens": ""})
    assert isinstance(empty.body, ScopeBody)
    assert empty.body.continued_from is None


def test_continued_from_reads_deepens_for_every_kind() -> None:
    scope = {"reason": "r"}
    kinds: list[tuple[dict[str, Any], str | None]] = [
        ({"id": "F1"}, None),
        ({"id": "F2", "deepens": "F1"}, "F1"),
        ({"id": "F3", "out_of_scope": scope}, None),
        ({"id": "F4", "out_of_scope": scope, "deepens": "F1"}, "F1"),
    ]
    for raw, expected in kinds:
        assert stored_finding(raw).body.continued_from_value() == expected


def test_theme_triage_shows_must_read_when_a_member_is_downgraded() -> None:
    members = [
        stored_finding({"id": "F1", "triage": "remaining"}),
        stored_finding({"id": "F2", "triage": "must_read"}),
    ]
    assert (
        theme_triage(
            StoredTheme(id="B1", title="", finding_ids=("F1", "F2"), severity="", triage=None),
            {member.body.id: member.triage for member in members},
        )
        is Triage.MUST_READ
    )


def test_judge_rebuttal_prompt_states_rules_once() -> None:
    text = (Path(triage.__file__).parent / "prompts" / "judge_rebuttal.md").read_text("utf-8")
    assert text.count("- verified:") == 1
    assert text.count("- tool_claim:") == 1


_SCOPE_RECORD = {"paths": ["x.py"], "reason": "範囲外"}


def _fill_source(triage_value: str | None, **extra: object) -> StoredFinding:
    raw: dict[str, Any] = {
        "id": "F1",
        "severity": "warning",
        "viewpoint": "v",
        "claim": "c",
        "adjudication": None,
        **extra,
    }
    if triage_value is not None:
        raw["triage"] = triage_value
    return stored_finding(raw)


def test_fill_triage_copies_the_stored_triage_without_deciding_again() -> None:
    # 保存済みの折りたたみは、規則を当て直さず写す（棄却の印があっても保存の区分が勝つ）
    finding = _fill_source("folded", severity="warning")
    assert fill_triage(MICRO, finding).decision == FoldedDecision()


def test_fill_triage_keeps_the_stored_scope_record() -> None:
    finding = _fill_source("out_of_scope", out_of_scope=_SCOPE_RECORD)
    assert fill_triage(MACRO, finding).decision == OutOfScopeDecision(out_of_scope=_SCOPE_RECORD)


def test_fill_triage_decides_again_for_scope_triage_without_record() -> None:
    finding = _fill_source("out_of_scope")
    assert isinstance(finding.body, RegularBody)
    filled = fill_triage(MACRO, finding)
    assert filled.decision == MustReadDecision()
    assert filled.decision.triage is Triage.MUST_READ


def test_fill_triage_decides_missing_triage_from_the_group_rule() -> None:
    nit = _fill_source(None, severity="nit")
    assert fill_triage(MICRO, nit).decision == FoldedDecision()
    assert fill_triage(SECURITY, nit).decision == RemainingDecision()
    assert fill_triage(MACRO, _fill_source(None)).decision == MustReadDecision()


def test_fill_triage_drops_the_rule_reason_outside_remaining() -> None:
    finding = _fill_source("must_read", triage_reason="weakened_unverified")
    assert fill_triage(MICRO, finding).decision == MustReadDecision()


def test_fill_triage_keeps_the_rule_reason_on_remaining() -> None:
    finding = _fill_source("remaining", triage_reason="weakened_unverified")
    decision = fill_triage(MICRO, finding).decision
    assert decision == RemainingDecision(reason=UNVERIFIED)
    assert decision.reason is UNVERIFIED


def test_fill_triage_keeps_an_unknown_reason_on_any_triage() -> None:
    finding = _fill_source("folded", triage_reason="from_the_future")
    decision = fill_triage(MICRO, finding).decision
    assert decision.reason == UnknownTriageReason("from_the_future")


def test_restored_decision_variants_per_triage() -> None:
    assert restored_decision(Triage.MUST_READ, None, None) == MustReadDecision()
    assert restored_decision(Triage.REJECTED, None, None) == RejectedDecision()
    assert restored_decision(Triage.REMAINING, None, TOOL_GUESS) == RemainingDecision(
        reason=TOOL_GUESS
    )
    assert restored_decision(Triage.OUT_OF_SCOPE, _SCOPE_RECORD, UNVERIFIED) == (
        OutOfScopeDecision(out_of_scope=_SCOPE_RECORD)
    )


def test_restored_decision_rejects_scope_triage_without_record() -> None:
    with pytest.raises(ValueError, match="記録が要ります"):
        restored_decision(Triage.OUT_OF_SCOPE, None, None)


_PINNED_REJECTED = {
    "severity": "critical",
    "adjudication": {
        "kind": "rebuttal",
        "challenge": "c",
        "recheck": [],
        "reason": "r",
        "verdict": "rejected",
    },
}


@pytest.mark.parametrize("group", [MACRO, MICRO, SECURITY])
def test_rejection_wins_in_every_group(group: GroupId) -> None:
    assert decided(group, _PINNED_REJECTED, theme_position=0) == RejectedDecision()
    with_scope = decided(group, _PINNED_REJECTED, scope_record=SCOPE_RECORD)
    assert with_scope == RejectedDecision()


@pytest.mark.parametrize("group", [MACRO, MICRO, SECURITY])
def test_scope_record_forces_out_of_scope_in_every_group(group: GroupId) -> None:
    warning = {"severity": "warning", "adjudication": None}
    nit = {"severity": "nit", "adjudication": None}
    for raw in (warning, nit):
        decision = decided(group, raw, theme_position=5, scope_record=SCOPE_RECORD)
        assert decision == OutOfScopeDecision(out_of_scope=SCOPE_RECORD)


def test_deepening_is_remaining_only_in_the_macro_group() -> None:
    deepened = {"severity": "warning", "adjudication": None, "deepens": "F0"}
    assert decided(MACRO, deepened) == RemainingDecision()
    assert decided(MACRO, {**deepened, "severity": "nit"}) == RemainingDecision()
    # 深掘りの印は方針の群の外では効かず、重大度と位置の規則に従う
    assert decided(MICRO, deepened, theme_position=0) == MustReadDecision()
    assert decided(MICRO, deepened, theme_position=3) == RemainingDecision()
    assert decided(MICRO, {**deepened, "severity": "nit"}) == FoldedDecision()
    assert decided(SECURITY, deepened) == MustReadDecision()
    assert decided(SECURITY, {**deepened, "severity": "nit"}) == RemainingDecision()


def test_scope_record_wins_over_deepening_in_the_macro_group() -> None:
    both = {"severity": "warning", "adjudication": None, "deepens": "F0"}
    decision = decided(MACRO, both, scope_record=SCOPE_RECORD)
    assert decision == OutOfScopeDecision(out_of_scope=SCOPE_RECORD)


def test_micro_position_boundary_and_downgrade_before_the_boundary() -> None:
    weakened = _rebuttal("weakened", verified=False)
    assert decided(MICRO, weakened, theme_position=2).reason is UNVERIFIED
    assert decided(MICRO, weakened, theme_position=3) == RemainingDecision()
    assert decided(MICRO, weakened, theme_position=None).reason is UNVERIFIED
    assert decided(MICRO, {"severity": "warning", "adjudication": None}, theme_position=2) == (
        MustReadDecision()
    )


def test_macro_group_never_downgrades_a_weakened_finding() -> None:
    assert decided(MACRO, _rebuttal("weakened", verified=False)) == MustReadDecision()
    assert decided(SECURITY, _rebuttal("weakened", verified=False)).reason is UNVERIFIED


def test_themes_schema_keeps_the_accepted_shape() -> None:
    assert triage.themes_schema() == {
        "type": "object",
        "additionalProperties": False,
        "required": ["themes"],
        "properties": {
            "themes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "finding_ids"],
                    "properties": {
                        "title": {"type": "string", "minLength": 1},
                        "finding_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }


@pytest.mark.parametrize(
    ("output", "accepted"),
    [
        ({"themes": []}, True),
        ({"themes": [{"title": "t", "finding_ids": ["a", "b"]}]}, True),
        ({"themes": [{"title": "t", "finding_ids": []}]}, True),
        ({}, False),
        ({"themes": "x"}, False),
        ({"themes": [{"title": "t"}]}, False),
        ({"themes": [{"finding_ids": []}]}, False),
        ({"themes": [{"title": "", "finding_ids": []}]}, False),
        ({"themes": [{"title": 1, "finding_ids": []}]}, False),
        ({"themes": [{"title": "t", "finding_ids": [1]}]}, False),
        ({"themes": [{"title": "t", "finding_ids": [1.0]}]}, False),
        ({"themes": [{"title": "t", "finding_ids": [True]}]}, False),
        ({"themes": [{"title": "t", "finding_ids": "a"}]}, False),
        ({"themes": [{"title": "t", "finding_ids": [], "extra": 1}]}, False),
        ({"themes": [], "extra": 1}, False),
    ],
)
def test_themes_model_accepts_what_the_schema_accepts(
    *, output: dict[str, Any], accepted: bool
) -> None:
    assert jsonschema.Draft202012Validator(triage.themes_schema()).is_valid(output) is accepted
    try:
        ThemesOutput.model_validate(output)
    except ValidationError:
        assert accepted is False
    else:
        assert accepted is True
