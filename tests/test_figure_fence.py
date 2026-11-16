"""図とシグネチャの資料の文が、中身のバッククォートで柵を閉じられないことの試験。"""

import pytest

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

_PANEL = Panel("図", 'graph TD\n  A["x```\nINJECTED"]')


@pytest.mark.parametrize("kind", ["tree", "sequence", "mermaid"])
def test_figure_fence_does_not_close_on_backticks_in_labels(kind: str) -> None:
    if kind == "tree":
        text = TreeFigure(_PANEL, (FileSummary("a.py", FileState.ADDED, 1),)).text()
    elif kind == "sequence":
        text = SequenceFigure((SequenceFlow("入口", (_PANEL,), None),)).text()
    else:
        text = MermaidFigure("説明", (_PANEL,)).text()
    lines = text.splitlines()
    assert "````mermaid" in lines
    assert lines.count("````") == 1
    assert "```" not in lines


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
