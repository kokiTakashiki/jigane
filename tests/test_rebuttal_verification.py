from typing import Any

import pytest
from triage_support import decided

from jigane.findings.records import (
    Rebuttal,
    TriageReason,
    UnreadableAdjudication,
    Verification,
    read_adjudication,
)
from jigane.groups.definitions import MICRO


def _raw(**extra: object) -> dict[str, Any]:
    return {
        "kind": "rebuttal",
        "challenge": "c",
        "recheck": [],
        "verdict": "weakened",
        "reason": "r",
        **extra,
    }


def _read(raw: dict[str, Any]) -> Rebuttal:
    adjudication = read_adjudication(raw)
    assert isinstance(adjudication, Rebuttal)
    return adjudication


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"verified": True}, Verification.VERIFIED),
        ({"verified": False}, Verification.NOT_VERIFIED),
        ({}, Verification.NO_RECORD),
    ],
)
def test_verified_field_is_read_as_three_states(
    extra: dict[str, Any], expected: Verification
) -> None:
    assert _read(_raw(**extra)).verified is expected


@pytest.mark.parametrize("extra", [{"verified": True}, {"verified": False}, {}])
def test_verified_field_round_trips_to_the_same_stored_shape(extra: dict[str, Any]) -> None:
    raw = _raw(**extra)
    written = _read(raw).to_json()
    assert written == raw
    assert ("verified" in written) is ("verified" in extra)


def test_non_boolean_verified_is_unreadable() -> None:
    assert isinstance(read_adjudication(_raw(verified="yes")), UnreadableAdjudication)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"verified": False}, TriageReason.WEAKENED_UNVERIFIED),
        ({"verified": True}, None),
        ({}, None),
    ],
)
def test_only_not_verified_is_downgraded(
    extra: dict[str, Any], expected: TriageReason | None
) -> None:
    decision = decided(
        MICRO, {"severity": "warning", "adjudication": _raw(**extra)}, theme_position=0
    )
    assert decision.reason is expected
