from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from jigane.judgment_map import changes
from jigane.judgment_map.changes import CHANGES_FILE, render_changes, without_whole_deletions
from jigane.judgment_map.classification import FilePair, is_whole_deletion
from jigane.judgment_map.configuration import Config
from jigane.reviews.lifecycle import Review
from jigane.skills import config_head, macro, sharded
from jigane.skills.shards import ShardPlan, ShardPlanKind

CHANGES = (
    "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n--- a/not_a_header\n"
    "--- a/kept.py\n+++ b/kept.py\n@@ -1 +1 @@\n-a\n+b\n"
    "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+c\n"
    "--- a/old.py\n+++ b/renamed.py\n@@ -1 +1 @@\n-d\n+e\n"
)
PAIRS = [
    FilePair("gone.py", None),
    FilePair("kept.py", "kept.py"),
    FilePair(None, "new.py"),
    FilePair("old.py", "renamed.py"),
]


def test_whole_deletion_body_is_dropped_and_listed() -> None:
    kept, removed = without_whole_deletions(CHANGES, PAIRS)
    assert removed == ["gone.py"]
    assert "x = 1" not in kept
    assert "--- a/gone.py" not in kept


def test_changed_added_and_renamed_sections_stay() -> None:
    kept, _ = without_whole_deletions(CHANGES, PAIRS)
    assert kept == CHANGES[CHANGES.index("--- a/kept.py") :]


def test_deletion_body_line_that_looks_like_a_header_does_not_split() -> None:
    kept, removed = without_whole_deletions(CHANGES, PAIRS)
    assert removed == ["gone.py"]
    assert "not_a_header" not in kept


def test_missing_header_is_an_error() -> None:
    with pytest.raises(ValueError, match="見出し"):
        without_whole_deletions("--- a/x\n+++ b/x\n", [FilePair("y", "y")])


@pytest.fixture
def diff_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    (tmp_path / CHANGES_FILE).write_text(CHANGES, encoding="utf-8")
    review = SimpleNamespace(id=SimpleNamespace(directory=lambda: tmp_path))
    monkeypatch.setattr(macro, "load_pairs", lambda _id: PAIRS)
    return cast("Review", review)


def test_verifier_changes_omit_deleted_body_and_list_paths(diff_review: Review) -> None:
    text = macro.verifier_changes_text(diff_review)
    assert "x = 1" not in text
    assert "### 丸ごと削除したファイル（本文は省いた）\n\n- gone.py\n" in text
    assert "+++ b/renamed.py" in text


def test_verifier_changes_without_deletion_equal_policy_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes = "--- a/kept.py\n+++ b/kept.py\n@@ -1 +1 @@\n-a\n+b\n"
    (tmp_path / CHANGES_FILE).write_text(changes, encoding="utf-8")
    monkeypatch.setattr(macro, "load_pairs", lambda _id: [FilePair("kept.py", "kept.py")])
    identifier = SimpleNamespace(directory=lambda: tmp_path)
    review = cast("Any", SimpleNamespace(id=identifier, is_diff=True))
    assert macro.verifier_changes_text(review) == macro.changes_prompt_text(review)


def test_policy_changes_keep_the_deleted_body(diff_review: Review) -> None:
    review = cast("Any", diff_review)
    review.is_diff = True
    assert "x = 1" in macro.changes_prompt_text(review)


def test_sharded_diff_plan_uses_trimmed_changes_and_full_plan_does_not(
    diff_review: Review, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = cast("Any", diff_review)
    review.is_diff = True
    review.repository_root = "."
    monkeypatch.setattr(config_head, "config_heads_section", lambda _root, _paths: "")
    diff_plan = ShardPlan(ShardPlanKind.DIFF, (), ())
    assert "x = 1" not in sharded._changes_with_config_heads(review, diff_plan)
    files_plan = ShardPlan(ShardPlanKind.FILES, (), ())
    assert sharded._changes_with_config_heads(review, files_plan) == macro.changes_prompt_text(
        review
    )


def test_discovery_and_verifier_share_the_deletion_criterion() -> None:
    assert is_whole_deletion(PAIRS[0])
    assert not any(is_whole_deletion(pair) for pair in PAIRS[1:])


def test_rendered_changes_split_with_excluded_and_over_limit_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 書く側（render_changes）の見出しと、切る側の見出しが一致することを、実際の出力で確かめる
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "gone.py").write_text("x = 1\n", encoding="utf-8")
    (base / "kept.py").write_text("a\n", encoding="utf-8")
    (head / "kept.py").write_text("b\n", encoding="utf-8")
    (head / "uv.lock").write_text("lock\n", encoding="utf-8")
    (head / "big.py").write_text("y = 2\n" * 20, encoding="utf-8")
    pairs = [
        FilePair("gone.py", None),
        FilePair("kept.py", "kept.py"),
        FilePair(None, "uv.lock"),
        FilePair(None, "big.py"),
    ]
    monkeypatch.setattr(changes, "CHANGES_CHAR_LIMIT", 120)
    text, omitted = render_changes(base, head, pairs, Config(exclude=("uv.lock",)))
    assert omitted >= 1
    kept, removed = without_whole_deletions(text, pairs)
    assert removed == ["gone.py"]
    assert "x = 1" not in kept
    assert "+++ b/kept.py" in kept
    assert "+++ b/uv.lock" in kept
    assert "+++ b/big.py" in kept
