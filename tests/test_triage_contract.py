"""区分の定義（findings/records.py）を正とし、schema・各面の出口に全部の区分が現れることを照合する。"""

import json
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError
from review_fakes import stored_finding

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.views.findings import macro_view_lines, security_view_lines
from jigane.findings.records import Triage, TriagedFinding, fill_triage
from jigane.groups.definitions import MACRO
from jigane.judgment_map.map_kind import CodeMap
from jigane.llm_runs.finding_model import Finding
from jigane.reviews.lifecycle import Review, create_feature_review

PACKAGE = Path(str(resources.files("jigane")))


TRIAGE_NAMES = [t.value for t in Triage]
TRIAGE_LABELS = {t.value: t.traits.label for t in Triage}


@pytest.fixture
def review_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return str(create_feature_review(root, ["src"], "p").id)


@pytest.mark.parametrize("group", ["macro", "micro", "security"])
def test_show_json_carries_the_triage_table_in_definition_order(
    review_name: str, group: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["show", review_name, group, "--json"]) == EXIT_OK
    triages = json.loads(capsys.readouterr().out)["triages"]
    assert triages == [
        {
            "name": t.value,
            "label": t.traits.label,
            "blocks_gate": t.traits.blocks_gate,
            "read_first": t.traits.read_first,
            "placement": t.traits.placement.value,
        }
        for t in Triage
        if group in t.traits.groups
    ]


def _finding_per_triage() -> list[TriagedFinding]:
    return [
        fill_triage(
            MACRO,
            stored_finding(
                {
                    "id": f"F{i}",
                    "severity": "warning",
                    "viewpoint": "M1",
                    "claim": f"claim-{name}",
                    "evidence": [{"path": "a.py", "start_line": 1, "end_line": 1}],
                    "adjudication": None,
                    "triage": Triage(name),
                    "judgment_region": None,
                    "out_of_scope": {"paths": ["b.py"], "reason": "r"},
                }
            ),
        )
        for i, name in enumerate(reversed(TRIAGE_NAMES), start=1)
    ]


def test_cli_views_list_every_triage_label_in_definition_order() -> None:
    review = cast(
        "Review",
        SimpleNamespace(
            map_kind=CodeMap.from_dict(
                {
                    "mode": "diff",
                    "segments": [],
                    "regions": [],
                    "line_counts": {"by_kind": {}, "by_level": {}},
                }
            )
        ),
    )
    for lines in (
        macro_view_lines(review, _finding_per_triage(), {}),
        security_view_lines(_finding_per_triage(), {}),
    ):
        text = "\n".join(lines)
        assert all(label in text for label in TRIAGE_LABELS.values())
        positions = [text.index(f"claim-{name}") for name in TRIAGE_NAMES]
        assert positions == sorted(positions)


def _base_finding() -> dict[str, Any]:
    return {
        "id": "F1",
        "severity": "warning",
        "group": "macro",
        "viewpoint": "design",
        "claim": "claim",
        "body": "body",
        "evidence": [{"path": "src/a.py", "start_line": 3, "end_line": 5}],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
        "adjudication": None,
        "theme": None,
    }


def test_model_requires_out_of_scope_record_for_that_triage() -> None:
    needing = [t for t in Triage if t.traits.needs_out_of_scope]
    assert needing
    for triage in needing:
        without_record = {**_base_finding(), "triage": triage.value}
        with pytest.raises(ValidationError, match="out_of_scope"):
            Finding.model_validate(without_record)
        with_record = {**without_record, "out_of_scope": {"paths": ["a"], "reason": "r"}}
        Finding.model_validate(with_record)


def _macro_finding(
    identifier: str, triage: str, region: str | None, **extra: object
) -> TriagedFinding:
    return fill_triage(
        MACRO,
        stored_finding(
            {
                "id": identifier,
                "severity": "warning",
                "viewpoint": "M1",
                "claim": f"claim-{identifier}",
                "evidence": [{"path": "a.py", "start_line": 1, "end_line": 1}],
                "adjudication": None,
                "triage": triage,
                "judgment_region": region,
                **extra,
            }
        ),
    )


def _review_with_regions() -> Review:
    regions = [
        SimpleNamespace(key="k1", id="J1", summary="入口"),
        SimpleNamespace(key="k2", id="J2", summary="出口"),
    ]
    return cast(
        "Review", SimpleNamespace(map_kind=SimpleNamespace(table=SimpleNamespace(regions=regions)))
    )


def test_macro_view_lines_pins_every_placement() -> None:
    scope = {"paths": ["b.py", "c.py"], "reason": "r1"}
    findings = [
        _macro_finding("F1", "must_read", "k2"),
        _macro_finding("F2", "must_read", "k1"),
        _macro_finding("F3", "must_read", None),
        _macro_finding("F4", "remaining", "k1"),
        _macro_finding("F5", "remaining", "unknown"),
        _macro_finding("F6", "out_of_scope", "k1", out_of_scope=scope),
        _macro_finding("F7", "out_of_scope", None, out_of_scope={"paths": [], "reason": "r2"}),
        _macro_finding("F8", "rejected", "k1"),
    ]
    evidence = "      根拠: a.py:1-1"
    assert macro_view_lines(_review_with_regions(), findings, {"F1": "*"}) == [
        "方針の指摘: 7 件（棄却 1 件）",
        "<必読>",
        "[J2 出口]",
        "  F1* warning M1 claim-F1",
        evidence,
        "[J1 入口]",
        "  F2 warning M1 claim-F2",
        evidence,
        "[変更全体]",
        "  F3 warning M1 claim-F3",
        evidence,
        "<残り>",
        "[J1 入口]",
        "  F4 warning M1 claim-F4",
        evidence,
        "[変更全体]",
        "  F5 warning M1 claim-F5",
        evidence,
        "<範囲外>",
        "範囲外: b.py, c.py",
        "      理由: r1",
        "  F6 warning M1 claim-F6",
        evidence,
        "範囲外: 範囲内の根拠のみ",
        "      理由: r2",
        "  F7 warning M1 claim-F7",
        evidence,
        "[棄却]",
        "  F8 warning M1 claim-F8",
        evidence,
    ]


def test_macro_view_lines_without_findings_prints_only_the_heading() -> None:
    assert macro_view_lines(_review_with_regions(), [], {}) == ["方針の指摘: 0 件（棄却 0 件）"]


def test_macro_view_lines_refuses_a_scope_section_member_without_a_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member = _macro_finding("F1", "must_read", None)
    monkeypatch.setattr(
        "jigane.commands.views.findings.group_by_triage",
        lambda _findings: [(Triage.OUT_OF_SCOPE, [member])],
    )
    with pytest.raises(TypeError, match="範囲外でない決定"):
        macro_view_lines(_review_with_regions(), [member], {})
