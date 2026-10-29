from types import SimpleNamespace
from typing import Any

import pytest

from jigane.commands.views import findings as findings_view
from jigane.commands.views import review as review_view
from jigane.pull_request_context.background import Background, PastPullRequest

HOSTILE_PATH = "src/\x1b[31ma\nFORGED 区間 x\tb.py"


def test_static_finding_segment_path_is_folded_to_one_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding = SimpleNamespace(
        id="S1", tool_name="ruff", rule_id="E1", level="error", message="m\nsecond"
    )
    segment = SimpleNamespace(
        new_path=HOSTILE_PATH, new_span=SimpleNamespace(start_line=1, end_line=2)
    )
    view = SimpleNamespace(
        store=SimpleNamespace(findings=[finding], dropped={}),
        raised_regions=1,
        outside_diff=(),
        by_segment=((segment, ("S1",)),),
    )
    monkeypatch.setattr(review_view, "_static_view", lambda _review: view)
    lines = review_view.static_finding_lines(None)  # type: ignore[arg-type]  # _static_view を差し替えるので review は引かれない
    assert "  区間 src/a FORGED 区間 x b.py:1-2" in lines
    assert all("\n" not in line and "\x1b" not in line for line in lines)


def test_finding_location_path_is_folded_to_one_line() -> None:
    place: dict[str, Any] = {"path": HOSTILE_PATH, "start_line": 3, "end_line": 4}
    assert findings_view.location(place) == "src/a FORGED 区間 x b.py:3-4"


def test_background_title_and_owners_are_folded_to_one_line() -> None:
    background = Background(
        documents=(),
        past_changes=(PastPullRequest(1, "t\x1b[2Jitle\nFORGED", ("a.py",)),),
        owners=(),
    )
    review = SimpleNamespace(background=background)
    lines = review_view.background_lines(review)  # type: ignore[arg-type]  # 背景だけを読む
    assert "  過去の PR #1 title FORGED（a.py）" in lines
    assert all("\n" not in line and "\x1b" not in line for line in lines)
