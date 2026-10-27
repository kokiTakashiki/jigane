from pathlib import Path
from typing import Any

import pytest
from review_fakes import make_review
from test_policy_classes import feature_review

from jigane.figures.models import (
    FileState,
    FileSummary,
    MermaidFigure,
    Panel,
    TreeFigure,
)
from jigane.policy_materials import changed_files
from jigane.policy_materials.changed_files import (
    FileChange,
    MovedAwayFileChange,
    MovedFileChange,
    PrebuiltSection,
    file_changes,
)
from jigane.skills import policy_figures
from jigane.skills.policy_figures import (
    LANGUAGE_AGNOSTIC,
    PROMPT_ORDER,
    REGISTRY,
    FigureSlot,
    PolicyFigure,
    PromptPlace,
)
from jigane.storage.store import ReviewKind


@pytest.mark.parametrize(
    ("state", "value", "label", "css"),
    [
        (FileState.ADDED, "added", "追加", "added"),
        (FileState.DELETED, "deleted", "削除", "deleted"),
        (FileState.CHANGED, "changed", "変更", "changed"),
        (FileState.MOVED, "moved", "移動", "moved"),
    ],
)
def test_file_state_values_labels_and_css(
    state: FileState, value: str, label: str, css: str
) -> None:
    assert (state.value, state.label, state.css_class) == (value, label, css)


def _segment(old: str | None, new: str | None, lines: int = 4) -> dict[str, Any]:
    span = {"start_line": 1, "end_line": lines}
    return {
        "old_path": old,
        "new_path": new,
        "old_span": span if old else None,
        "new_span": span if new else None,
        "old_line_count": lines if old else 0,
        "new_line_count": lines if new else 0,
        "qualified_name": "",
        "declaration_kind": None,
    }


def _changes(
    tmp_path: Path, *segments: dict[str, Any], before: bool = False
) -> dict[str, FileChange]:
    review = make_review(
        tmp_path,
        [],
        judgment_map={"segments": list(segments), "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )
    return file_changes(review, before=before)


def test_state_is_decided_once_from_the_segments(tmp_path: Path) -> None:
    segments = (
        _segment(None, "added.py", 3),
        _segment("deleted.py", None, 5),
        _segment("both.py", "both.py"),
        _segment("both.py", "both.py"),
        _segment("moved.py", None),
        _segment("moved.py", "moved.py"),
    )
    changes = _changes(tmp_path, *segments)
    assert changes["added.py"] == FileChange(FileState.ADDED, 3)
    assert changes["deleted.py"] == FileChange(FileState.DELETED, 5)
    assert changes["both.py"] == FileChange(FileState.CHANGED, 8)
    assert changes["moved.py"].state is FileState.CHANGED
    assert [c.exists_after for c in (changes["added.py"], changes["deleted.py"])] == [True, False]


def test_before_states_skip_added_files(tmp_path: Path) -> None:
    segments = (
        _segment(None, "added.py"),
        _segment("deleted.py", None, 5),
        _segment("both.py", "both.py", 2),
    )
    changes = _changes(tmp_path, *segments, before=True)
    assert "added.py" not in changes
    assert changes["deleted.py"] == FileChange(FileState.DELETED, 5)
    assert changes["both.py"] == FileChange(FileState.CHANGED, 2)


def test_file_list_text_uses_the_label() -> None:
    figure = TreeFigure(Panel("変更後の木", ""), (FileSummary("a.py", FileState.ADDED, 3),))
    assert "a.py（追加、3 行）" in figure.text()


def _fake(title: str, slot: PromptPlace) -> PolicyFigure:
    figure = MermaidFigure(title, ())
    return PolicyFigure(title, title, lambda _review: figure, slot, interface=LANGUAGE_AGNOSTIC)


def test_registry_order_decides_the_section_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = feature_review(tmp_path, "m.py", "x = 1\n")
    prebuilt = {section: f"<{section.name}>" for section in PrebuiltSection}
    x = _fake("X", FigureSlot.AFTER_TREE)
    y = _fake("Y", FigureSlot.AFTER_TREE)
    z = _fake("Z", FigureSlot.AFTER_REGIONS)
    monkeypatch.setattr(policy_figures, "REGISTRY", (x, y, z))
    assert policy_figures.prompt_figure_texts(review, prebuilt) == [
        "<TREE>",
        "### X\n\nX",
        "### Y\n\nY",
        "<SEQUENCE>",
        "<REGIONS>",
        "### Z\n\nZ",
    ]
    monkeypatch.setattr(policy_figures, "REGISTRY", (y, x, z))
    swapped = policy_figures.prompt_figure_texts(review, prebuilt)
    assert swapped.index("### Y\n\nY") < swapped.index("### X\n\nX")


def test_every_registry_slot_has_a_place() -> None:
    assert {entry.slot for entry in REGISTRY} <= set(PROMPT_ORDER)


def test_prompt_order_alternates_the_two_kinds_of_place_once_each() -> None:
    assert len(PROMPT_ORDER) == len(set(PROMPT_ORDER))
    assert {p for p in PROMPT_ORDER if isinstance(p, PrebuiltSection)} == set(PrebuiltSection)
    assert {p for p in PROMPT_ORDER if isinstance(p, FigureSlot)} == set(FigureSlot)


def test_materials_sections_are_keyed_by_the_prebuilt_section(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "m.py", "x = 1\n")
    sections = changed_files.materials_sections(review)
    assert set(sections) == {PrebuiltSection.TREE, PrebuiltSection.REGIONS}
    assert sections[PrebuiltSection.REGIONS].startswith(f"### {PrebuiltSection.REGIONS.value}\n")


def test_a_file_with_different_old_and_new_paths_is_moved(tmp_path: Path) -> None:
    changes = _changes(
        tmp_path,
        _segment("old/dir/a.py", "new/a.py", 6),
        _segment("old/dir/a.py", "new/a.py", 6),
        _segment("same.py", "same.py"),
    )
    assert changes["new/a.py"] == MovedFileChange(FileState.MOVED, 12, "old/dir/a.py")
    assert "old/dir/a.py" not in changes
    assert changes["same.py"].state is FileState.CHANGED


def test_the_before_tree_counts_a_moved_file_at_its_old_path(tmp_path: Path) -> None:
    changes = _changes(tmp_path, _segment("old/a.py", "new/a.py"), before=True)
    assert changes == {"old/a.py": MovedAwayFileChange(FileState.MOVED, 4, "new/a.py")}
