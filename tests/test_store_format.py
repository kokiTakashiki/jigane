from collections.abc import Mapping
from typing import Any

import pytest
from store_format_support import check_upgrades

from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import (
    FormatState,
    Upgrade,
    classify_version,
    stored_version,
    upgrade_mapping,
)


def _add(key: str) -> Upgrade:
    def upgrade(raw: Mapping[str, Any]) -> dict[str, Any]:
        return {**raw, key: True}

    return upgrade


UPGRADES: dict[int, Upgrade] = {1: _add("v2"), 2: _add("v3")}


def _run(raw: Mapping[str, Any], current: int = 3) -> dict[str, Any]:
    return upgrade_mapping("x.json", raw, current=current, upgrades=UPGRADES)


def test_unstamped_is_version_one() -> None:
    assert _run({"a": 1}) == {"a": 1, "v2": True, "v3": True}


def test_each_step_applies_from_stored_version() -> None:
    assert _run({"format": 2, "a": 1}) == {"a": 1, "v3": True}
    assert _run({"format": 3, "a": 1}) == {"a": 1}


def test_format_key_is_removed_from_result() -> None:
    assert "format" not in _run({"format": 1})


def test_input_is_not_mutated() -> None:
    raw = {"format": 1, "a": 1}
    _run(raw)
    assert raw == {"format": 1, "a": 1}


@pytest.mark.parametrize("version", ["2", 1.0, None, True, False, [1]])
def test_non_integer_version_is_rejected(version: object) -> None:
    with pytest.raises(ReviewError, match=r"x\.json.*整数ではありません"):
        _run({"format": version})


@pytest.mark.parametrize("version", [0, -1])
def test_version_below_one_is_rejected(version: int) -> None:
    with pytest.raises(ReviewError, match=r"x\.json.*1 以上"):
        _run({"format": version})


def test_future_version_is_rejected() -> None:
    with pytest.raises(ReviewError, match=r"x\.json.*より新しい"):
        _run({"format": 4})


def test_check_upgrades_accepts_complete_table() -> None:
    check_upgrades("x.json", 3, UPGRADES)
    check_upgrades("x.json", 1, {})


@pytest.mark.parametrize("table", [{1: _add("a")}, {2: _add("a"), 1: _add("b"), 3: _add("c")}])
def test_check_upgrades_rejects_gaps_and_extras(table: dict[int, Upgrade]) -> None:
    with pytest.raises(ReviewError, match=r"x\.json.*移しの表"):
        check_upgrades("x.json", 3, table)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, FormatState.OLDER),
        ({"format": 1}, FormatState.OLDER),
        ({"format": 3}, FormatState.CURRENT),
        ({"format": 4}, FormatState.NEWER),
        ({"format": 0}, FormatState.INVALID),
        ({"format": "3"}, FormatState.INVALID),
        ({"format": True}, FormatState.INVALID),
        ([], FormatState.OLDER),
    ],
)
def test_classify_version_by_stored_version(raw: object, expected: FormatState) -> None:
    assert classify_version(stored_version(raw), 3) is expected
