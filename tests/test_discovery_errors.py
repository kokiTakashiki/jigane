import pytest

from jigane.skills import discovery_errors, macro


def test_an_input_budget_error_cannot_be_made_without_its_stop() -> None:
    with pytest.raises(TypeError):
        discovery_errors.InputBudgetError(macro.GROUP, "message")  # type: ignore[call-arg]
