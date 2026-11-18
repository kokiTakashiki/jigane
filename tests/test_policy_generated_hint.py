from dataclasses import replace
from pathlib import Path

from pr_support import commit_all, git
from review_fakes import make_review

from jigane.git.tree_files import MAX_REVIEW_FILE_BYTES
from jigane.policy_materials import changed_files
from jigane.policy_materials.changed_files import GENERATED_LEGEND, PrebuiltSection
from jigane.policy_materials.generated_marks import (
    NO_GENERATED_HINT,
    GeneratedHint,
    MarksRead,
    MarksUnreadable,
    generated_hint,
)
from jigane.reviews.lifecycle import LocalRange, RangeOrigin, Review
from jigane.skills import policy_figures
from jigane.storage.store import ReviewKind


def range_review(tmp_path: Path, attributes: str | None) -> tuple[Review, tuple[Path, str]]:
    """変更後の木に a.py、履歴の head に attributes の .gitattributes を持つ範囲のレビュー。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "a.py").write_text("x\n", encoding="utf-8")
    if attributes is not None:
        (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    sha = commit_all(repo, "head").strip()
    tree = tmp_path / "stored-tree"
    tree.mkdir()
    (tree / "a.py").write_text("x\n", encoding="utf-8")
    base = tmp_path / "stored-base"
    base.mkdir()
    span = {"start_line": 1, "end_line": 1}
    segment = {
        "old_path": "a.py",
        "new_path": "a.py",
        "old_span": span,
        "new_span": span,
        "old_line_count": 1,
        "new_line_count": 1,
    }
    review = make_review(
        tree,
        ["a.py"],
        kind=ReviewKind.RANGE,
        base_tree=base,
        judgment_map={
            "segments": [segment],
            "regions": [{"id": "J1", "path": "a.py"}],
            "line_counts": {},
        },
    )
    origin = review.origin
    assert isinstance(origin, RangeOrigin)
    review.origin = RangeOrigin(
        LocalRange(origin.range.range_text, str(repo)), replace(origin.diff, head_sha=sha)
    )
    return review, (repo, sha)


def hint_of(review: Review, repo: Path, head: str) -> GeneratedHint:
    base = review.base_tree_path
    assert base is not None
    return generated_hint(repo, head, ["a.py"], base)


def test_marked_file_gets_label_legend_and_region_column(tmp_path: Path) -> None:
    review, source = range_review(tmp_path, "/a.py linguist-generated\n")
    hint = hint_of(review, *source)
    assert isinstance(hint.reading, MarksRead)
    assert hint.generated == frozenset({"a.py"})
    sections = changed_files.materials_sections(review, folded=True, hint=hint)
    tree = sections[PrebuiltSection.TREE]
    assert "<br/>生成物" in tree
    assert GENERATED_LEGEND in tree
    regions = sections[PrebuiltSection.REGIONS]
    assert "| 種類 | 生成物 | パス |" in regions
    assert "| linguist-generated |" in regions
    assert "<br/>生成物" in changed_files.tree_figure(review, hint).text()


def test_unmarked_tree_keeps_label_legend_and_table_shape(tmp_path: Path) -> None:
    review, source = range_review(tmp_path, None)
    hint = hint_of(review, *source)
    assert isinstance(hint.reading, MarksRead)
    assert hint.generated == frozenset()
    marked = changed_files.materials_sections(review, folded=True, hint=hint)
    plain = changed_files.materials_sections(review, folded=True)
    assert marked == plain
    assert GENERATED_LEGEND not in marked[PrebuiltSection.TREE]
    assert "生成物" not in marked[PrebuiltSection.REGIONS]


def test_excluded_marked_file_gets_no_label(tmp_path: Path) -> None:
    review, source = range_review(tmp_path, "/a.py linguist-generated\n")
    base = review.base_tree_path
    assert base is not None
    (base / ".jigane.toml").write_text('exclude = ["*.py"]\n', encoding="utf-8")
    assert hint_of(review, *source).generated == frozenset()


def test_review_hint_reads_the_range_head(tmp_path: Path) -> None:
    review, _ = range_review(tmp_path, "/a.py linguist-generated\n")
    assert policy_figures._generated_hint(review).generated == frozenset({"a.py"})


def test_review_without_head_reads_nothing(tmp_path: Path) -> None:
    review = make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE)
    assert policy_figures._generated_hint(review) == NO_GENERATED_HINT


def test_head_not_shaped_as_sha_is_unreadable(tmp_path: Path) -> None:
    review, (repo, _) = range_review(tmp_path, "/a.py linguist-generated\n")
    hint = hint_of(review, repo, "HEAD")
    assert isinstance(hint.reading, MarksUnreadable)
    assert hint.generated == frozenset()


def test_missing_head_is_unreadable_and_labels_nothing(tmp_path: Path) -> None:
    review, (repo, _) = range_review(tmp_path, "/a.py linguist-generated\n")
    hint = hint_of(review, repo, "0" * 40)
    assert isinstance(hint.reading, MarksUnreadable)
    assert hint.generated == frozenset()


def test_marks_section_lists_mark_lines_and_definitions_not_macro_uses(tmp_path: Path) -> None:
    attributes = (
        "# comment linguist-generated\n"
        "[attr]generated linguist-generated\n"
        "* generated\n"
        "/a.py linguist-generated\n"
        "/b linguist-generated=false\n"
        "sub/** -linguist-generated\n"
    )
    review, source = range_review(tmp_path, attributes)
    sections = changed_files.materials_sections(review, folded=True, hint=hint_of(review, *source))
    marks = sections[PrebuiltSection.MARKS]
    assert "| `.gitattributes` | `[attr]generated` | `linguist-generated` |" in marks
    assert "| `.gitattributes` | `/a.py` | `linguist-generated` |" in marks
    assert "`linguist-generated=false`" in marks
    assert "`-linguist-generated`" in marks
    assert "`*`" not in marks
    assert "comment" not in marks


def test_marks_section_caps_rows_and_cuts_long_patterns(tmp_path: Path) -> None:
    long_pattern = "p" * 200
    rows = "".join(f"/f{index} linguist-generated\n" for index in range(31))
    review, source = range_review(tmp_path, rows + f"{long_pattern} linguist-generated\n")
    marks = changed_files.marks_text(hint_of(review, *source))
    assert "ほか 2 件" in marks
    assert "/f29" in marks
    assert "/f30" not in marks
    long_dir = tmp_path / "long"
    long_dir.mkdir()
    long_review, (long_repo, long_head) = range_review(
        long_dir, f"{long_pattern} linguist-generated\n"
    )
    long_marks = changed_files.marks_text(hint_of(long_review, long_repo, long_head))
    assert "p" * 160 in long_marks
    assert "p" * 161 not in long_marks


def test_unreadable_marks_say_so_in_the_section(tmp_path: Path) -> None:
    review, (repo, _) = range_review(tmp_path, "/a.py linguist-generated\n")
    sections = changed_files.materials_sections(
        review, folded=True, hint=hint_of(review, repo, "0" * 40)
    )
    assert sections[PrebuiltSection.MARKS].endswith(changed_files.MARKS_UNREADABLE_TEXT)
    assert "<br/>生成物" not in sections[PrebuiltSection.TREE]


def test_no_gitattributes_has_no_marks_section(tmp_path: Path) -> None:
    review, source = range_review(tmp_path, None)
    sections = changed_files.materials_sections(review, folded=True, hint=hint_of(review, *source))
    assert PrebuiltSection.MARKS not in sections


def test_oversized_gitattributes_is_named_as_code(tmp_path: Path) -> None:
    review, (repo, head) = range_review(tmp_path, "/a.py linguist-generated\n")
    big = repo / "x#&y" / ".gitattributes"
    big.parent.mkdir()
    big.write_text("#" * (MAX_REVIEW_FILE_BYTES + 1), encoding="utf-8")
    head = commit_all(repo, "big").strip()
    marks = changed_files.marks_text(hint_of(review, repo, head))
    assert "`x#&y/.gitattributes` が大きすぎて表に載せない。" in marks
    assert "| `.gitattributes` | `/a.py` | `linguist-generated` |" in marks
