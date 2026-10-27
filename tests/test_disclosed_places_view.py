from typing import Any

from review_fakes import stored_finding

from jigane.commands.subcommands.show import _with_derived_nit_reason
from jigane.commands.views.findings import finding_lines
from jigane.findings.discard import (
    DISCARDED_VIEWPOINT,
    NO_CONCERN_CLAIM,
    DisclosedNitReason,
    ExtractedConcern,
    NitReason,
    NoConcern,
    concern_with_places,
    places_line,
)
from jigane.findings.records import TriagedFinding, fill_triage
from jigane.groups.definitions import MICRO
from jigane.publishing.rendering import _finding_context


def _disclosed(places: tuple[str, ...]) -> NitReason:
    return DisclosedNitReason("主張", ExtractedConcern("主張"), places)


def _triaged(reason: NitReason, viewpoint: str = "") -> TriagedFinding:
    raw: dict[str, Any] = {
        "id": "F1",
        "severity": "nit",
        "viewpoint": viewpoint,
        "group": "micro",
        "evidence": [],
        "nit_reason": reason.to_json(),
    }
    return fill_triage(MICRO, stored_finding(raw))


def test_concern_with_places_appends_the_places_joined_by_the_separator() -> None:
    assert concern_with_places(_disclosed(("A", "B"))) == "主張（開示の場所: A、B）"


def test_concern_with_places_is_the_bare_concern_without_places() -> None:
    assert concern_with_places(_disclosed(())) == "主張"


def test_show_json_carries_places_and_the_derived_concern_text() -> None:
    shown = _with_derived_nit_reason(_triaged(_disclosed(("A", "B"))))["nit_reason"]
    assert shown["places"] == ["A", "B"]
    assert shown["concern_text"] == "主張（開示の場所: A、B）"


def test_show_text_line_includes_the_places() -> None:
    lines = finding_lines(_triaged(_disclosed(("A", "B"))), {})
    assert "主張（開示の場所: A、B）" in lines[0]


def _without_concern(places: tuple[str, ...]) -> NitReason:
    return DisclosedNitReason("元の文", NoConcern(), places)


def test_concern_with_places_is_none_without_a_concern_and_places() -> None:
    assert concern_with_places(_without_concern(())) is None


def test_concern_with_places_is_none_without_a_concern_even_with_places() -> None:
    # 元の文は捨てた理由の文なので、懸念の文の代わりにしない
    assert concern_with_places(_without_concern(("A",))) is None


def test_show_json_omits_the_concern_text_without_a_concern_and_places() -> None:
    shown = _with_derived_nit_reason(_triaged(_without_concern(())))["nit_reason"]
    assert "concern_text" not in shown


def test_show_json_carries_the_concern_text_for_an_extracted_concern_without_places() -> None:
    shown = _with_derived_nit_reason(_triaged(_disclosed(())))["nit_reason"]
    assert shown["concern_text"] == "主張"


def test_show_text_without_a_concern_does_not_substitute_the_text() -> None:
    lines = finding_lines(_triaged(_without_concern(("A",))), {})
    assert "元の文" not in "".join(lines)
    assert lines == [f"  F1 nit  {NO_CONCERN_CLAIM}", "      開示の場所: A"]
    assert finding_lines(_triaged(_without_concern(())), {}) == [f"  F1 nit  {NO_CONCERN_CLAIM}"]


def test_show_json_blanks_the_claim_without_a_concern() -> None:
    shown = _with_derived_nit_reason(_triaged(_without_concern(("A",))))
    assert shown["claim"] == NO_CONCERN_CLAIM
    assert "concern_text" not in shown["nit_reason"]
    assert "label" not in shown["nit_reason"]


def test_report_context_does_not_substitute_the_text_for_a_missing_concern() -> None:
    with_concern = _finding_context(_triaged(_disclosed(("A",))), None)
    assert with_concern["claim"] == "主張（開示の場所: A）"
    without = _finding_context(_triaged(_without_concern(("A",))), None)
    assert without["claim"] == NO_CONCERN_CLAIM


def test_report_context_hides_the_discarded_viewpoint_only() -> None:
    copied = _triaged(_disclosed(()), DISCARDED_VIEWPOINT)
    other = _triaged(_disclosed(()), "D1")
    assert _finding_context(copied, None)["viewpoint"] == ""
    assert _finding_context(other, None)["viewpoint"] == "D1"
    # show の JSON は機械が読む印として残す
    assert _with_derived_nit_reason(copied)["viewpoint"] == DISCARDED_VIEWPOINT


def test_show_text_line_does_not_show_the_discarded_viewpoint() -> None:
    copied = _triaged(_disclosed(()), DISCARDED_VIEWPOINT)
    assert "discarded" not in "".join(finding_lines(copied, {}))
    assert finding_lines(_triaged(_disclosed(()), "D1"), {})[0] == "  F1 nit D1 主張"


def test_places_line_joins_the_places_with_the_separator() -> None:
    assert places_line(_disclosed(("A", "B"))) == "開示の場所: A、B"


def test_places_line_is_none_without_places() -> None:
    assert places_line(_disclosed(())) is None


def test_show_json_carries_the_places_line() -> None:
    shown = _with_derived_nit_reason(_triaged(_disclosed(("A", "B"))))["nit_reason"]
    assert shown["places_line"] == "開示の場所: A、B"


def test_show_json_omits_the_places_line_without_places() -> None:
    shown = _with_derived_nit_reason(_triaged(_disclosed(())))["nit_reason"]
    assert "places_line" not in shown


def test_report_context_carries_the_places_line_only_without_a_concern() -> None:
    without = _finding_context(_triaged(_without_concern(("A", "B"))), None)
    assert without["places_line"] == "開示の場所: A、B"
    assert _finding_context(_triaged(_disclosed(("A",))), None)["places_line"] is None
