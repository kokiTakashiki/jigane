import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from jigane.findings.records import (
    MACHINE_SCOPE_REASON,
    ConcludedInScope,
    ConcludedOutOfScope,
    NoConclusion,
    ScopeConclusion,
    judge_scope,
)
from jigane.reviews.lifecycle import (
    PAIRS_FILE,
    DiffSource,
    PullRequestInfo,
    PullRequestOrigin,
    Review,
    create_feature_review,
    scope_files,
)
from jigane.skills.scope import InScope, OutOfScope, classify, conclusion_of


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _diff_review(project: Path, paths: list[str]) -> Review:
    feature = create_feature_review(project, ["src"], "p")
    return replace(
        feature,
        paths=paths,
        origin=PullRequestOrigin(PullRequestInfo("u", 1, "t"), DiffSource("b", "h", "m", "x")),
    )


def test_diff_scope_collects_old_and_new_paths_including_deleted(project: Path) -> None:
    review = _diff_review(project, ["fallback"])
    pairs = [["old/a.py", "new/a.py"], ["gone.py", None], [None, "added.py"]]
    (review.id.directory() / PAIRS_FILE).write_text(json.dumps(pairs), encoding="utf-8")
    assert scope_files(review) == {"old/a.py", "new/a.py", "gone.py", "added.py"}


def test_diff_scope_without_pairs_falls_back_to_paths(project: Path) -> None:
    review = _diff_review(project, ["src/a.py"])
    (review.id.directory() / PAIRS_FILE).unlink(missing_ok=True)
    assert scope_files(review) == {"src/a.py"}


def test_feature_scope_is_given_paths(project: Path) -> None:
    assert scope_files(create_feature_review(project, ["src"], "p")) == {"src/a.py"}


def test_judge_scope_machine_decides_only_without_concluded_judgment() -> None:
    verdict = judge_scope(["lib/b.py", "lib/b.py"], {"src/a.py"}, NoConclusion())
    assert verdict == {"paths": ["lib/b.py"], "reason": MACHINE_SCOPE_REASON}


def test_judge_scope_concluded_in_scope_wins_over_machine_decision() -> None:
    assert judge_scope(["lib/b.py"], {"src/a.py"}, ConcludedInScope()) is None


def test_judge_scope_without_judgment_and_with_inside_path_stays_in_scope() -> None:
    assert judge_scope(["src/a.py", "lib/b.py"], {"src/a.py"}, NoConclusion()) is None


def test_judge_scope_concluded_out_keeps_outside_paths_and_reason() -> None:
    verdict = judge_scope(
        ["src/a.py", "lib/b.py"], {"src/a.py"}, ConcludedOutOfScope("共通部が先")
    )
    assert verdict == {"paths": ["lib/b.py"], "reason": "共通部が先"}


def _candidate(**scope_fields: object) -> dict[str, Any]:
    return {"evidence": [{"path": "lib/b.py"}], **scope_fields}


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"in_scope": True, "scope_reason": ""}, ConcludedInScope()),
        ({"in_scope": False, "scope_reason": "共通部が先"}, ConcludedOutOfScope("共通部が先")),
        # 範囲外なのに理由が無い出力は、範囲内に倒す
        ({"in_scope": False, "scope_reason": ""}, ConcludedInScope()),
        ({"in_scope": False}, ConcludedInScope()),
        ({}, NoConclusion()),
    ],
)
def test_conclusion_of_reads_output_fields_into_state(
    fields: dict[str, object], expected: ScopeConclusion
) -> None:
    assert conclusion_of(_candidate(**fields)) == expected


def test_classify_out_of_scope_without_reason_stays_in_scope() -> None:
    result = classify(_candidate(in_scope=False, scope_reason=""), {"src/a.py"})
    assert isinstance(result, InScope)


def test_classify_without_fields_falls_back_to_machine_decision() -> None:
    outside = classify(_candidate(), {"src/a.py"})
    assert isinstance(outside, OutOfScope)
    assert outside.record["reason"] == MACHINE_SCOPE_REASON
    assert isinstance(classify(_candidate(), {"lib/b.py"}), InScope)


def test_out_of_scope_conclusion_needs_reason() -> None:
    with pytest.raises(ValueError, match="理由"):
        ConcludedOutOfScope("")
