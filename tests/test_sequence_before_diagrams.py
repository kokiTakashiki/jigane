"""変更前の図の結果は、図か無い理由のどちらか一方を持つ。"""

from typing import Any

from jigane.sequence.before_diagrams import (
    BeforeAbsence,
    BeforeFound,
    BeforeMissing,
    attach_before,
)


def test_a_before_result_holds_either_a_diagram_or_a_reason() -> None:
    model: dict[str, Any] = {"diagrams": [{}, {}]}
    found: dict[str, Any] = {"arrows": []}
    attach_before(model, [BeforeFound(found), BeforeMissing(BeforeAbsence.NO_ROOT)])
    assert (model["diagrams"][0]["before"], model["diagrams"][0]["before_note"]) == (found, None)
    assert (model["diagrams"][1]["before"], model["diagrams"][1]["before_note"]) == (
        None,
        BeforeAbsence.NO_ROOT,
    )
