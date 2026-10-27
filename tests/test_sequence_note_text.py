"""シーケンス図の注記は、表示の敬体と資料の常体を一つの値で持つ。"""

import dataclasses
import json
from typing import Any, cast

import pytest

from jigane.figures.models import NoteText, Panel, SequenceFigure, SequenceFlow
from jigane.sequence.review_diagrams import BeforeAbsence
from jigane.sequence.roots import SequenceError

NOTE = NoteText("変更前にこの入口が無い", "変更前にこの入口がありません")


def test_flow_json_carries_the_display_form_and_text_carries_the_material_form() -> None:
    figure = SequenceFigure((SequenceFlow("E", (Panel("変更後", "sequenceDiagram"),), NOTE),))
    flow_json = json.loads(json.dumps(figure.to_json()))["flows"][0]
    assert flow_json["note"] == "変更前にこの入口がありません"
    assert "変更前の図は無い: 変更前にこの入口が無い" in figure.text()
    assert "ありません" not in figure.text()


def test_figure_notes_json_carries_the_display_form_and_text_carries_the_material_form() -> None:
    note = NoteText("図が無い。", "図がありません。")
    figure = SequenceFigure((), (note,))
    assert figure.to_json()["notes"] == ["図がありません。"]
    assert figure.text() == "図が無い。"


def test_a_note_cannot_be_made_from_one_form_only() -> None:
    incomplete = cast("Any", NoteText)
    with pytest.raises(TypeError):
        incomplete("資料だけ")
    with pytest.raises(TypeError):
        incomplete(display="表示だけ")
    with pytest.raises(dataclasses.FrozenInstanceError):
        NOTE.display = "別の文"  # type: ignore[misc]


def test_every_before_absence_has_both_forms() -> None:
    for absence in BeforeAbsence:
        note = absence.note
        assert note.display == str(absence)
        assert note.material
        assert note.material != note.display


def test_sequence_error_keeps_both_forms() -> None:
    error = SequenceError(NOTE)
    assert str(error) == NOTE.display
    assert error.note == NOTE
