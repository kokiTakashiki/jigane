"""実装の群の表示の分類と、全ての指摘を畳まずに出す表示。"""

import json
from pathlib import Path
from typing import Any

import pytest
from fakes import MicroFake, clear_policy, d_finding
from review_fakes import stored_finding
from test_triage import project, with_severity  # noqa: F401 pytest が fixture を名前で引くため import が要る

from jigane.commands import main
from jigane.commands.output import display_category_table
from jigane.commands.views.findings import micro_view_lines
from jigane.findings.discard import DisclosedNitReason, ExtractedConcern
from jigane.findings.records import (
    DisplayCategory,
    NitReasonIs,
    Otherwise,
    Triage,
    TriagedFinding,
    display_category_of,
    display_info,
    fill_triage,
)
from jigane.groups.definitions import MICRO
from jigane.reviews.lifecycle import create_feature_review

EXIT_OK = 0
SCOPE = {"paths": ["x.py"], "reason": "範囲の外"}


def _finding(
    number: int, triage: Triage, nit_reason: dict[str, Any] | None = None
) -> TriagedFinding:
    raw: dict[str, Any] = {
        "id": f"F{number}",
        "severity": "warning",
        "claim": f"claim{number}",
        "evidence": [],
        "triage": triage,
    }
    if nit_reason is not None:
        raw["nit_reason"] = nit_reason
    if triage is Triage.OUT_OF_SCOPE:
        raw["out_of_scope"] = SCOPE
    return fill_triage(MICRO, stored_finding(raw))


def _disclosed() -> dict[str, Any]:
    return DisclosedNitReason("限界", ExtractedConcern("限界"), ("README",)).to_json()


def test_display_category_follows_triage_and_nit_reason() -> None:
    cases = {
        DisplayCategory.MUST_READ: _finding(1, Triage.MUST_READ),
        DisplayCategory.UNVERIFIED: _finding(7, Triage.UNVERIFIED),
        DisplayCategory.REMAINING: _finding(2, Triage.REMAINING),
        DisplayCategory.NIT: _finding(3, Triage.FOLDED),
        DisplayCategory.DISCLOSED_LIMIT: _finding(4, Triage.FOLDED, _disclosed()),
        DisplayCategory.OUT_OF_SCOPE: _finding(5, Triage.OUT_OF_SCOPE),
        DisplayCategory.REJECTED: _finding(6, Triage.REJECTED),
    }
    for expected, finding in cases.items():
        assert display_category_of(finding) is expected


def test_display_categories_are_ordered_and_labelled() -> None:
    assert [(c.value, display_info(c).label) for c in DisplayCategory] == [
        ("must_read", "必読"),
        ("unverified", "未検証"),
        ("remaining", "残り"),
        ("nit", "nit"),
        ("disclosed_limit", "開示済みの限界"),
        ("out_of_scope", "範囲外"),
        ("rejected", "棄却"),
    ]


def test_only_must_read_and_remaining_are_themed() -> None:
    assert [c.value for c in DisplayCategory if display_info(c).themed] == [
        "must_read",
        "unverified",
        "remaining",
    ]
    assert {c["name"]: c["themed"] for c in display_category_table()} == {
        c.value: display_info(c).themed for c in DisplayCategory
    }


def test_display_categories_map_one_to_one_to_triages_except_folded() -> None:
    owners = [(entry.category, triage) for triage in Triage for entry in triage.traits.displays]
    # 表示の分類は一度ずつ現れ、並びは区分の宣言の順をたどる
    assert [category for category, _ in owners] == list(DisplayCategory)
    assert {t: sum(1 for _, o in owners if o is t) for t in Triage} == {
        t: 2 if t is Triage.FOLDED else 1 for t in Triage
    }


def test_display_traits_agree_with_triage_traits() -> None:
    # 期待は実装の式を使わず、分類ごとに直に書く（分類、名札、テーマで束ねるか、元の区分）
    expected = {
        DisplayCategory.MUST_READ: ("必読", True, Triage.MUST_READ),
        DisplayCategory.UNVERIFIED: ("未検証", True, Triage.UNVERIFIED),
        DisplayCategory.REMAINING: ("残り", True, Triage.REMAINING),
        DisplayCategory.NIT: ("nit", False, Triage.FOLDED),
        DisplayCategory.DISCLOSED_LIMIT: ("開示済みの限界", False, Triage.FOLDED),
        DisplayCategory.OUT_OF_SCOPE: ("範囲外", False, Triage.OUT_OF_SCOPE),
        DisplayCategory.REJECTED: ("棄却", False, Triage.REJECTED),
    }
    assert {
        c: (display_info(c).label, display_info(c).themed, display_info(c).source)
        for c in DisplayCategory
    } == expected


def test_each_triage_has_one_otherwise_and_only_folded_splits() -> None:
    for triage in Triage:
        displays = triage.traits.displays
        assert sum(1 for d in displays if isinstance(d.split, Otherwise)) == 1
        assert (len(displays) > 1) is (triage is Triage.FOLDED)
    assert [d.split for d in Triage.FOLDED.traits.displays] == [
        Otherwise(),
        NitReasonIs(DisclosedNitReason),
    ]


def test_text_shows_every_category_without_folding() -> None:
    findings = [
        _finding(1, Triage.MUST_READ),
        _finding(3, Triage.FOLDED),
        _finding(4, Triage.FOLDED, _disclosed()),
        _finding(5, Triage.OUT_OF_SCOPE),
        _finding(6, Triage.REJECTED),
    ]
    text = "\n".join(micro_view_lines(findings, [], {}))
    assert "<必読> 1 件" in text
    assert "<残り>" not in text
    for heading in ("<nit> 1 件", "<開示済みの限界> 1 件", "<範囲外> 1 件", "<棄却> 1 件"):
        assert heading in text
    for number in (1, 3, 5, 6):
        assert f"claim{number}" in text
    assert "限界（開示の場所: README）" in text
    assert "折りたたみ" not in text
    positions = [text.index(f"<{c}>") for c in ("必読", "nit", "範囲外", "棄却")]
    assert positions == sorted(positions)
    assert "実装の指摘: 5 件（うち棄却 1 件）" in text


def test_text_places_the_unverified_section_with_its_count_right_after_must_read() -> None:
    findings = [
        _finding(1, Triage.MUST_READ),
        _finding(2, Triage.REMAINING),
        _finding(3, Triage.UNVERIFIED),
        _finding(4, Triage.UNVERIFIED),
    ]
    lines = micro_view_lines(findings, [], {})
    headings = [line for line in lines if line.startswith("<")]
    assert headings == ["<必読> 1 件", "<未検証> 2 件", "<残り> 1 件"]
    text = "\n".join(lines)
    assert text.index("claim3") > text.index("<未検証> 2 件") > text.index("claim1")
    assert text.index("claim4") < text.index("<残り>")


def test_text_has_no_unverified_section_without_unverified_findings() -> None:
    text = "\n".join(micro_view_lines([_finding(1, Triage.MUST_READ)], [], {}))
    assert "未検証" not in text


def test_rejected_only_run_is_not_reported_as_no_findings() -> None:
    text = "\n".join(micro_view_lines([_finding(1, Triage.REJECTED)], [], {}))
    assert "実装の指摘: 1 件（うち棄却 1 件）" in text
    assert "指摘なし" not in text


def test_show_json_carries_category_and_table(
    project: Path,  # noqa: F811 import した fixture を引数で受けて注入させる
    capsys: pytest.CaptureFixture[str],
) -> None:
    review = create_feature_review(project, ["src"], "p")
    clear_policy(review)
    name = str(review.id)
    fake = MicroFake({1: [d_finding("D1"), with_severity(d_finding("D8"), "nit", "tiny")]})
    assert main(["run", name, "micro"], invoke=fake) == EXIT_OK
    capsys.readouterr()
    assert main(["show", name, "micro", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert [t["name"] for t in data["display_categories"]][:3] == [
        "must_read",
        "unverified",
        "remaining",
    ]
    assert [t["themed"] for t in data["display_categories"]][:3] == [True, True, True]
    assert {f["display_category"] for f in data["findings"]} == {"must_read", "nit"}
    assert "triages" in data
    assert main(["show", name, "micro"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "tiny" in out
    assert "<nit> 1 件" in out
