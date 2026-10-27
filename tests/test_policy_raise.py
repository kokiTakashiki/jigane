import json
from pathlib import Path
from typing import Any

import pytest
from review_fakes import stored_finding
from triage_support import assigned

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings.records import (
    FindingKind,
    Triage,
    kind_of,
    load_findings_state,
    triaged_dicts,
)
from jigane.groups.definitions import MACRO, GroupId
from jigane.llm_runs.finding_model import Finding
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro


def macro_finding(number: int, **extra: object) -> dict[str, Any]:
    return {
        "id": f"F{number}",
        "severity": "warning",
        "group": "macro",
        "viewpoint": "M2",
        "claim": f"claim{number}",
        "body": f"body{number}",
        "evidence": [{"path": "src/a.py", "start_line": 1, "end_line": 2}],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
        "adjudication": None,
        "theme": None,
        "triage": "must_read",
        **extra,
    }


def test_prompt_raises_scope_and_deepening_instead_of_dropping_them() -> None:
    parts = macro.prompt_parts()
    standards, history = parts["standards"], parts["history_rule"]
    assert "in_scope を false にして scope_reason" in standards
    assert "deepens" in standards
    assert "必読に数えません" in standards
    assert "deepens に前の回の番号" in history
    assert "その行の番号を deepens に引き継ぎます" in history
    assert "body の初めに `@名前 F1` と書いて" in history
    assert "同じ論点の深掘りなので出しません" not in history
    assert "捨てる理由は、根拠の位置を示せないもの" in standards
    assert "上の出す指摘の基準の捨てる理由に従います" in history
    assert "決着した形を元へ戻させる" not in history


def test_deepens_is_an_optional_field_of_the_model_output() -> None:
    conclude = macro.conclude_schema()["properties"]["findings"]["items"]
    assert "deepens" in conclude["properties"]


def test_deepening_and_out_of_scope_do_not_count_as_must_read() -> None:
    assert assigned(MACRO, macro_finding(1)) is Triage.MUST_READ
    deepening = assigned(MACRO, macro_finding(2, deepens="F1"))
    assert deepening is Triage.REMAINING
    record = {"paths": ["docs/x.md"], "reason": "設計書の外を直す"}
    scoped = assigned(MACRO, macro_finding(3, deepens="F1"), scope_record=record)
    assert scoped is Triage.OUT_OF_SCOPE
    for triage in (deepening, scoped):
        assert not triage.traits.read_first
        assert not triage.traits.blocks_gate


def test_schema_accepts_deepens_and_old_findings_without_it() -> None:
    Finding.model_validate(macro_finding(2, deepens="F1"))
    Finding.model_validate(macro_finding(1))
    with pytest.raises(ValueError, match="deepens"):
        Finding.model_validate(macro_finding(3, deepens="前の回"))


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n" + "x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    created = create_feature_review(root, ["src"], "p")
    scoped = macro_finding(
        2,
        triage="out_of_scope",
        out_of_scope={"paths": ["docs/x.md"], "reason": "設計書の外を直す必要がある"},
    )
    state = {
        "last_number": 3,
        "last_theme_number": 0,
        "stale_groups": [],
        "groups": {
            "macro": [
                macro_finding(1),
                scoped,
                macro_finding(3, triage="remaining", deepens="F1"),
            ]
        },
        "themes": [],
    }
    (created.id.directory() / "findings.json").write_text(json.dumps(state), encoding="utf-8")
    return created


def test_old_findings_without_deepens_load(review: Review) -> None:
    state = load_findings_state(review.id.directory())
    macro_findings = triaged_dicts(state.groups[GroupId("macro")])
    assert [f["triage"] for f in macro_findings] == [
        Triage.MUST_READ,
        Triage.OUT_OF_SCOPE,
        Triage.REMAINING,
    ]
    assert "deepens" not in macro_findings[0]


def test_reply_separates_scope_and_deepening_from_must_read(
    review: Review, tmp_path: Path
) -> None:
    assert main(["pick", str(review.id), "F1", "F2", "F3"]) == EXIT_OK
    out = tmp_path / "out.md"
    assert (
        main(["export-report", str(review.id), "--template", "reply", "--out", str(out)])
        == EXIT_OK
    )
    text = out.read_text(encoding="utf-8")
    scope_at = text.index("### 範囲外の懸念")
    deepen_at = text.index("### 前の回の指摘の続き")
    assert (
        text.index("**F1**") < scope_at < text.index("**F2**") < deepen_at < text.index("**F3**")
    )
    assert "範囲外の理由: 設計書の外を直す必要がある" in text
    assert "前の回の指摘: F1" in text


def test_finding_kind_is_decided_in_one_place() -> None:
    assert stored_finding(macro_finding(1)).body.kind is FindingKind.REGULAR
    assert stored_finding(macro_finding(2, deepens="F1")).body.kind is FindingKind.DEEPENING
    record = {"paths": ["docs/x.md"], "reason": "設計書の外を直す"}
    assert kind_of(scope_record=record, continued_from="F1") is FindingKind.SCOPE
    assert [k.traits.heading for k in FindingKind] == [None, "範囲外の懸念", "前の回の指摘の続き"]


def test_show_json_carries_kind_for_each_finding(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["show", str(review.id), "macro", "--json"]) == EXIT_OK
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert {f["id"]: (f["kind"], f["kind_mark"]) for f in findings} == {
        "F1": ("regular", None),
        "F2": ("scope", "範囲外"),
        "F3": ("deepening", "深掘り"),
    }
