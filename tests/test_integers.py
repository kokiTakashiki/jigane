from typing import Any

import pytest

from jigane.preconditions.integers import non_negative_int, positive_int
from jigane.skills.adversarial import _line_range
from jigane.skills.disclosed import vet_groups


@pytest.mark.parametrize("value", [True, False, "3", 1.0, None, [1], -1])
def test_non_negative_int_rejects_non_integers_and_negatives(value: object) -> None:
    assert non_negative_int(value) is None


@pytest.mark.parametrize("value", [0, 1, 99])
def test_non_negative_int_passes_zero_and_up(value: int) -> None:
    assert non_negative_int(value) == value


@pytest.mark.parametrize("value", [True, False, 0, -3, "3", 1.0, None])
def test_positive_int_rejects_bool_zero_negative_and_strings(value: object) -> None:
    assert positive_int(value) is None


@pytest.mark.parametrize("value", [1, 2, 99])
def test_positive_int_passes_one_and_up(value: int) -> None:
    assert positive_int(value) == value


@pytest.mark.parametrize(
    ("start", "end"),
    [(True, True), (False, True), (1, True), (0, 1), (-1, 1), ("3", 3), (3, "3"), (2, 1)],
)
def test_line_range_rejects_bad_positions(start: object, end: object) -> None:
    assert _line_range({"path": "a.py", "start_line": start, "end_line": end}) is None


def test_line_range_passes_positive_integers() -> None:
    assert _line_range({"path": "a.py", "start_line": 1, "end_line": 3}) == (1, 3, "a.py")


def test_vet_groups_rejects_negative_and_out_of_range_positions() -> None:
    vetted = vet_groups([[-1, 1], [0, 3], [0, 4]], 3)
    assert vetted.groups == ()
    assert vetted.discarded == 3


def test_vet_groups_passes_zero_and_the_last_valid_position() -> None:
    vetted = vet_groups([[0, 2]], 3)
    assert vetted.groups == ((0, 2),)
    assert vetted.discarded == 0


def test_vet_groups_rejects_bool_positions() -> None:
    bad: list[list[Any]] = [[True, 2], [0, False]]
    assert vet_groups(bad, 3).groups == ()
