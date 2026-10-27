import json
from pathlib import Path

import pytest
from review_fakes import make_review, with_map

from jigane.figures.models import (
    TREE_DIFF_INTRO,
    TREE_INTRO,
    DiffTreeFigure,
    FileState,
    Panel,
    TreeFigure,
)
from jigane.policy_materials import changed_files
from jigane.policy_materials.changed_files import FileChange, MovedAwayFileChange, MovedFileChange
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput
from jigane.storage.store import ReviewKind


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text(
        "def build_widget(\n    size: int,\n) -> int:\n    secret_body_line = 1\n"
        "    return size\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    return create_feature_review(root, ["src"], "p")


def entry(*, added: bool = False, deleted: bool = False, lines: int = 3) -> FileChange:
    state = FileState.ADDED if added else FileState.DELETED if deleted else FileState.CHANGED
    return FileChange(state, lines)


def _tree_text(files: dict[str, FileChange]) -> str:
    panel = Panel("変更後の木", changed_files.tree_mermaid(files))
    return TreeFigure(panel, changed_files.file_summaries(files)).text()


def test_tree_marks_changed_added_deleted_and_lists_every_file() -> None:
    files = {
        "src/a.py": entry(),
        "src/b.py": entry(added=True),
        "old/c.py": entry(deleted=True),
    }
    text = _tree_text(files)
    diagram = text.split("```")[1]
    assert "flowchart LR" in diagram
    assert ":::added" in diagram
    assert ":::deleted" in diagram
    assert ":::changed" in diagram
    assert "変更の合計は 3 ファイル、9 行" in text


def test_tree_draws_every_file_however_many() -> None:
    count = 120
    files = {f"pkg{i % 3}/f{i}.py": entry() for i in range(count)}
    diagram = _tree_text(files).split("```")[1]
    assert all(f"f{i}.py<br/>" in diagram for i in range(count))


def test_tree_section_explains_the_tree_and_its_marks(review: Review) -> None:
    section = macro.policy_sections(review).figures[changed_files.PrebuiltSection.TREE]
    intro = TREE_INTRO
    assert section.startswith(f"### 変更のツリー図\n\n{intro}")
    for word in ("ツリー", "追加", "削除", "変更", "移動", "移動元", "描かない", "縮め"):
        assert word in intro
    assert "-->" in section
    assert "flowchart LR" in section


def test_feature_tree_figure_is_one_after_tree(review: Review) -> None:
    figure = changed_files.tree_figure(review)
    assert not isinstance(figure, DiffTreeFigure)
    assert figure.after.mermaid == changed_files.tree_mermaid(
        changed_files.file_changes(review), changed_files.region_tags(review)
    )
    panel = {"title": "変更後の木", "mermaid": figure.after.mermaid}
    assert figure.to_json() == {"kind": "tree", "title": "ツリー図", "after": panel}
    assert "####" not in figure.text()


def test_prompt_carries_materials_not_diff(review: Review) -> None:
    prompt = macro.build_diagram_prompt(AssemblyInput.load(review))
    assert "### 変更のツリー図" in prompt
    assert "### 公開のシグネチャの変更前後" not in prompt
    assert "### 判断の箇所の一覧" in prompt
    assert "secret_body_line" not in prompt
    assert "変更前は空" not in prompt


def _tree(files: dict[str, FileChange], tags: dict[str, list[str]] | None = None) -> list[str]:
    return changed_files.tree_mermaid(files, tags).split("\n")


def test_tree_is_empty_without_files() -> None:
    assert changed_files.tree_mermaid({}) == ""


def test_tree_collapses_a_chain_into_one_node_including_the_root() -> None:
    lines = _tree({"src/jigane/skills/a.py": entry(lines=12)})
    assert lines[0] == "flowchart LR"
    assert lines[1] == '    n0["src/jigane/skills/"]'
    assert lines[2] == '    n1["a.py<br/>変更・12 行"]:::changed'
    assert lines[3] == "    n0 --> n1"


def test_tree_keeps_a_directory_with_direct_files_and_a_forked_root() -> None:
    lines = _tree({"src/a.py": entry(), "src/sub/deep/b.py": entry(), "docs/c.md": entry()})
    text = "\n".join(lines)
    assert '["./"]' in text
    assert '["src/"]' in text
    assert '["sub/deep/"]' in text
    assert '["docs/"]' in text
    assert "src/sub" not in text


def test_tree_marks_the_four_states_and_the_moved_origin() -> None:
    files = {
        "a/added.py": entry(added=True),
        "a/gone.py": entry(deleted=True),
        "a/kept.py": entry(),
        "a/moved.py": MovedFileChange(FileState.MOVED, 12, "old/dir/moved.py"),
    }
    text = "\n".join(_tree(files))
    assert 'added.py<br/>追加・3 行"]:::added' in text
    assert 'gone.py<br/>削除・3 行"]:::deleted' in text
    assert 'kept.py<br/>変更・3 行"]:::changed' in text
    assert 'moved.py<br/>移動・12 行<br/>移動元: old/dir/moved.py"]:::moved' in text
    assert "classDef moved" in text
    assert "fill" not in text
    assert text.count(" --> ") == 4


def test_tree_adds_region_tags_and_escapes_labels() -> None:
    files = {"a/b.py": entry(), "a/x<y>.py": entry()}
    text = "\n".join(_tree(files, {"a/b.py": ["J1", "J2"]}))
    assert "変更・3 行<br/>J1 J2" in text
    assert "x#lt;y#gt;.py" in text


def test_tree_source_is_deterministic_in_path_order() -> None:
    files = {"b/y.py": entry(), "a/x.py": entry(), "a/w.py": entry()}
    first = changed_files.tree_mermaid(files)
    assert first == changed_files.tree_mermaid(dict(reversed(files.items())))
    assert first.index("w.py") < first.index("x.py") < first.index("y.py")


def test_regions_table_keeps_key_column(review: Review) -> None:
    assert "| 箇所 | key |" in changed_files.regions_text(review)


def test_regions_list_has_no_code(review: Review) -> None:
    text = changed_files.regions_text(review)
    assert "| J1 |" in text
    assert "src/a.py" in text
    assert "secret_body_line" not in text


def _diff_review(tmp_path: Path) -> Review:
    segments = [
        _segment(None, "a/added.py", 3),
        _segment("a/gone.py", None, 5),
        _segment("a/kept.py", "a/kept.py", 2),
        _segment("old/moved.py", "new/moved.py", 4),
    ]
    regions = [
        {"id": "J1", "path": "a/gone.py"},
        {"id": "J2", "path": "a/kept.py"},
        {"id": "J3", "path": "a/added.py"},
    ]
    base = tmp_path / "base"
    base.mkdir()
    return make_review(
        tmp_path,
        [],
        base_tree=base,
        judgment_map={"segments": segments, "regions": regions, "line_counts": {}},
        kind=ReviewKind.RANGE,
    )


def _segment(old: str | None, new: str | None, lines: int) -> dict[str, object]:
    span = {"start_line": 1, "end_line": lines}
    return {
        "old_path": old,
        "new_path": new,
        "old_span": span if old else None,
        "new_span": span if new else None,
        "old_line_count": lines if old else 0,
        "new_line_count": lines if new else 0,
    }


def test_before_changes_put_a_moved_file_at_its_origin_with_its_destination(
    tmp_path: Path,
) -> None:
    before = changed_files.file_changes(_diff_review(tmp_path), before=True)
    assert before["old/moved.py"] == MovedAwayFileChange(FileState.MOVED, 4, "new/moved.py")
    assert "new/moved.py" not in before


def test_diff_tree_has_a_before_tree_and_an_after_tree(tmp_path: Path) -> None:
    figure = changed_files.tree_figure(_diff_review(tmp_path))
    assert isinstance(figure, DiffTreeFigure)
    before, after = figure.before.mermaid, figure.after.mermaid
    # 追加は変更後にだけ、削除は変更前にだけ、変更は両方に出る
    assert "added.py" in after
    assert "added.py" not in before
    assert "gone.py" in before
    assert "gone.py" not in after
    assert "kept.py" in before
    assert "kept.py" in after
    # 移動は変更前では移動元に移動先を、変更後では移動先に移動元を添える
    assert 'moved.py<br/>移動・4 行<br/>移動先: new/moved.py"]:::moved' in before
    assert 'moved.py<br/>移動・4 行<br/>移動元: old/moved.py"]:::moved' in after
    assert '["old/"]' in before
    assert '["new/"]' not in before
    assert '["new/"]' in after
    assert '["old/"]' not in after


def test_region_tags_attach_to_the_tree_whose_node_has_the_path(tmp_path: Path) -> None:
    figure = changed_files.tree_figure(_diff_review(tmp_path))
    assert isinstance(figure, DiffTreeFigure)
    # 削除したファイルの箇所は旧パスなので変更前の木に、追加したファイルの箇所は変更後の木に付く
    assert "gone.py<br/>削除・5 行<br/>J1" in figure.before.mermaid
    assert "J1" not in figure.after.mermaid
    assert "J3" in figure.after.mermaid
    assert "J3" not in figure.before.mermaid
    assert "J2" in figure.before.mermaid
    assert "J2" in figure.after.mermaid


def test_diff_tree_json_names_both_trees_and_has_no_tree_alias(tmp_path: Path) -> None:
    figure = changed_files.tree_figure(_diff_review(tmp_path))
    assert isinstance(figure, DiffTreeFigure)
    payload = figure.to_json()
    assert payload["before"] == {"title": "変更前の木", "mermaid": figure.before.mermaid}
    assert payload["after"] == {"title": "変更後の木", "mermaid": figure.after.mermaid}
    assert "tree" not in payload


def test_diff_tree_text_splits_the_two_trees_and_keeps_the_file_list(
    tmp_path: Path,
) -> None:
    figure = changed_files.tree_figure(_diff_review(tmp_path))
    text = figure.text()
    assert text.startswith(TREE_DIFF_INTRO)
    assert text.index("#### 変更前") < text.index("#### 変更後")
    assert text.count("```mermaid") == 2
    assert "変更の合計は 4 ファイル" in text
    assert "- a/added.py（追加、3 行）" in text
    for word in ("変更前の木", "変更後の木", "移動先", "移動元", "削除は変更前の木にだけ"):
        assert word in TREE_DIFF_INTRO


def test_diff_tree_says_when_the_before_tree_is_empty(tmp_path: Path) -> None:
    review = _diff_review(tmp_path)
    only_added = {
        **review.map_kind.to_dict(),
        "segments": [_segment(None, "a/added.py", 3)],
        "regions": [],
    }
    text = changed_files.tree_figure(with_map(review, only_added)).text()
    assert "変更前の木に置くファイルは無い。" in text
    assert text.count("```mermaid") == 1


def _with_pairs(
    tmp_path: Path, pairs: list[list[str | None]], segments: list[dict[str, object]]
) -> Review:
    review = with_map(
        _diff_review(tmp_path), {"segments": segments, "regions": [], "line_counts": {}}
    )
    directory = review.id.directory()
    directory.mkdir(parents=True)
    (directory / "pairs.json").write_text(json.dumps(pairs), encoding="utf-8")
    return review


def test_a_pure_move_without_segments_appears_in_both_trees(tmp_path: Path) -> None:
    review = _with_pairs(
        tmp_path,
        [["old/queries.py", "new/queries.py"], ["a/kept.py", "a/kept.py"]],
        [_segment("a/kept.py", "a/kept.py", 2)],
    )
    after = changed_files.file_changes(review)
    before = changed_files.file_changes(review, before=True)
    assert after["new/queries.py"] == MovedFileChange(FileState.MOVED, 0, "old/queries.py")
    assert before["old/queries.py"] == MovedAwayFileChange(FileState.MOVED, 0, "new/queries.py")
    figure = changed_files.tree_figure(review)
    assert isinstance(figure, DiffTreeFigure)
    # 行数が 0 の移動の札は「移動」と移動元（移動先）だけで、「0 行」を書かない
    assert 'queries.py<br/>移動<br/>移動元: old/queries.py"]:::moved' in figure.after.mermaid
    assert 'queries.py<br/>移動<br/>移動先: new/queries.py"]:::moved' in figure.before.mermaid
    assert "0 行" not in figure.after.mermaid + figure.before.mermaid


def test_changed_files_say_changed_in_the_before_tree_too(tmp_path: Path) -> None:
    figure = changed_files.tree_figure(_diff_review(tmp_path))
    assert isinstance(figure, DiffTreeFigure)
    assert "kept.py<br/>変更・2 行" in figure.before.mermaid
    assert "変更前・" not in figure.before.mermaid


def test_folded_tree_is_one_tree_of_the_changed_branches_with_deleted_files(
    tmp_path: Path,
) -> None:
    figure = changed_files.folded_tree_figure(_diff_review(tmp_path))
    assert not isinstance(figure, DiffTreeFigure)
    mermaid = figure.after.mermaid
    # 変更前の木にだけあった削除と、変更後の木にだけあった追加が、同じ木に載る
    assert "gone.py" in mermaid
    assert "added.py" in mermaid
    assert "kept.py" in mermaid
    assert 'moved.py<br/>移動・4 行<br/>移動元: old/moved.py"]:::moved' in mermaid
    text = figure.text()
    assert text.count("```mermaid") == 1
    assert "#### 変更前" not in text
    assert "#### 変更後" not in text


def test_policy_sections_carry_the_folded_tree_for_a_diff_review(tmp_path: Path) -> None:
    tree = macro.policy_sections(_diff_review(tmp_path)).figures[
        changed_files.PrebuiltSection.TREE
    ]
    assert tree.count("```mermaid") == 1
    assert "#### 変更前" not in tree
