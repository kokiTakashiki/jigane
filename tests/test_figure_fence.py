"""図とシグネチャの資料の文が、中身のバッククォートで柵を閉じられないことの試験。"""

import pytest

from jigane.design.division import section_marks
from jigane.design.parse import parse_design
from jigane.figures.models import (
    AddedSignature,
    FileState,
    FileSummary,
    MermaidFigure,
    MovedSignature,
    Panel,
    SequenceFigure,
    SequenceFlow,
    SignatureFigure,
    TreeFigure,
)
from jigane.policy_materials.compact import ChangeNodes
from jigane.policy_materials.design_doc_structure import structure_text
from jigane.skills import macro_compact

_PANEL = Panel("図", 'graph TD\n  A["見本\n```\n### 見本の見出し\n終わり"]')


@pytest.mark.parametrize("kind", ["tree", "sequence", "mermaid"])
def test_figure_fence_does_not_close_on_backticks_in_labels(kind: str) -> None:
    if kind == "tree":
        text = TreeFigure(_PANEL, (FileSummary("a.py", FileState.ADDED, 1),)).text()
    elif kind == "sequence":
        text = SequenceFigure((SequenceFlow("入口", (_PANEL,), None),)).text()
    else:
        text = MermaidFigure("説明", (_PANEL,)).text()
    lines = text.splitlines()
    start = lines.index("````mermaid")
    end = lines.index("````")
    assert lines.count("````") == 1
    assert lines[start + 1 : end] == _PANEL.mermaid.splitlines()


@pytest.mark.parametrize(
    "entry",
    [
        AddedSignature("f", "a.py", "def f(x: '```\nINJECTED')"),
        MovedSignature("f", "a.py", "b.py", "def f(x: '```\nINJECTED')"),
    ],
)
def test_signature_fence_does_not_close_on_backticks_in_signatures(entry: object) -> None:
    lines = SignatureFigure((entry,)).text().splitlines()  # type: ignore[arg-type]
    assert "````" in lines
    assert "```" not in lines


def test_design_diagram_labels_stay_in_the_structure_fence() -> None:
    document = parse_design(
        "# 設計\n~~~~mermaid\nsequenceDiagram\nparticipant A as 見本 ```` ### 見本の見出し\n~~~~\n"
    )
    text = structure_text(document, section_marks(document.sections), ())
    opener, entry, closer = text.split("Mermaid の図:\n\n", 1)[1].splitlines()
    assert opener == closer == "`````"
    assert "見本 ```` ### 見本の見出し" in entry


def test_sequence_material_contains_fenced_labels_and_single_line_titles() -> None:
    title = "入口\n### 見本の見出し"
    panel = Panel(
        "変更後",
        "sequenceDiagram\n  participant P1 as 見本 ```` ### 見本の見出し\n"
        "  participant P2 as 宛先\n  P1->>P2: [J1] 呼び出し",
    )
    sequence = SequenceFigure((SequenceFlow(title, (panel,), None),))
    nodes = ChangeNodes("", {})
    for text in (
        sequence.text(),
        macro_compact._sequence_text(sequence, nodes),
        macro_compact._boundary_calls(sequence, nodes),
    ):
        lines = text.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("`````"))
        end = lines.index("`````", start + 1)
        assert any("見本 ```` ### 見本の見出し" in line for line in lines[start + 1 : end])
        assert any("入口\\n### 見本の見出し" in line for line in lines)
        assert "### 見本の見出し" not in lines
    assert sequence.to_json()["flows"][0]["title"] == title
