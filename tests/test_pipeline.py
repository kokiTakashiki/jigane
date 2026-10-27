from pathlib import Path
from typing import Any

import pytest
from fakes import FakeInvoke, discovered, settings_for
from pydantic import ValidationError
from review_fakes import stored_finding

from jigane.findings.records import (
    Alternative,
    Rebuttal,
    StoredFinding,
    ToolBasis,
    UnreadableAdjudication,
    Verification,
    read_adjudication,
    reserve_numbers,
)
from jigane.groups.definitions import Effort
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import pipeline


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return create_feature_review(root, ["src"], "p")


def shape(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        **candidate,
        "adjudication": None,
        "group": "macro",
        "theme": None,
        "triage": "must_read",
    }


def test_keep_valid_counts_schema_misses_and_consumes_their_numbers(review: Review) -> None:
    broken = {**discovered(), "severity": "not-a-severity"}
    drops = pipeline.DropCounts()
    candidates = [
        pipeline.ValidatedCandidate(discovered("M2"), None),
        pipeline.ValidatedCandidate(broken, None),
        pipeline.ValidatedCandidate(discovered("M3"), None),
    ]
    kept = pipeline.keep_valid(review, candidates, drops, shape)
    assert [item.body.id for item in kept] == ["F1", "F3"]
    assert drops.invalid_schema == 1
    assert reserve_numbers(review.id.directory(), 1) == 4


def test_run_state_head_carries_the_common_items() -> None:
    drops = pipeline.DropCounts(without_evidence=2, invalid_schema=1)
    head = pipeline.run_state_head(
        pipeline.PolicyGroupRun,
        settings=settings_for(FakeInvoke(), effort=Effort.LOW),
        invocations=[],
        discovered=5,
        drops=drops,
        elapsed_s=316.24,
    ).to_json()
    assert {key: head[key] for key in head if key != "ran_at"} == {
        "elapsed_s": 316.2,
        "model": "m",
        "effort": "low",
        "invocations": [],
        "discovered": 5,
        "dropped_without_evidence": 2,
        "dropped_invalid_schema": 1,
    }
    assert head["ran_at"]


@pytest.mark.parametrize(
    "broken",
    [{"recheck": None}, {"tool_claim": 3}, {"verdict": "nonsense"}],
)
def test_read_adjudication_returns_unreadable_for_wrong_typed_values(
    broken: dict[str, Any],
) -> None:
    raw = {"kind": "rebuttal", "challenge": "c", "recheck": [], "verdict": "kept", "reason": "r"}
    assert isinstance(read_adjudication({**raw, **broken}), UnreadableAdjudication)


def test_model_output_rebuttal_is_built_through_the_adjudication_model(tmp_path: Path) -> None:
    place = {"path": "src/a.py", "start_line": 1, "end_line": 2}
    raw = {
        "kind": "rebuttal",
        "challenge": "c",
        "recheck": [place],
        "verdict": "kept",
        "reason": "r",
        "verified": True,
        "tool_claim": {"relies": True, "basis": "docs", "version": "1"},
    }
    built = pipeline.adjudication_from_model_output(raw, tmp_path)
    assert isinstance(built, Rebuttal)
    assert built.recheck == (place,)
    assert built.verified is Verification.VERIFIED
    assert built.tool_claim is not None
    assert built.tool_claim.basis is ToolBasis("docs")
    assert built.to_json() == raw


def test_model_output_alternative_is_built_through_the_adjudication_model(tmp_path: Path) -> None:
    raw = {
        "kind": "alternative",
        "alternative": "a",
        "defense": "d",
        "verdict": "kept",
        "reason": "r",
    }
    built = pipeline.adjudication_from_model_output(raw, tmp_path)
    assert isinstance(built, Alternative)
    assert built.to_json() == raw


def test_model_output_that_misses_the_model_raises_validation_error(tmp_path: Path) -> None:
    raw = {
        "kind": "rebuttal",
        "challenge": "c",
        "recheck": [],
        "verdict": "nonsense",
        "reason": "r",
    }
    with pytest.raises(ValidationError):
        pipeline.adjudication_from_model_output(raw, tmp_path)


def test_alternative_keeps_rebuttal_only_keys_in_extra() -> None:
    raw = {
        "kind": "alternative",
        "alternative": "a",
        "defense": "d",
        "verdict": "kept",
        "reason": "r",
        "verified": True,
    }
    read = read_adjudication(raw)
    assert isinstance(read, Alternative)
    assert read.extra == {"verified": True}
    assert read.to_json()["verified"] is True


def test_stored_finding_takes_the_adjudication_from_one_source() -> None:
    raw = {
        "id": "F1",
        "severity": "warning",
        "viewpoint": "V",
        "claim": "C",
        "theme": None,
        "triage": None,
    }
    adjudication = read_adjudication(
        {
            "kind": "alternative",
            "alternative": "a",
            "defense": "d",
            "verdict": "kept",
            "reason": "r",
        }
    )
    assert isinstance(adjudication, Alternative)
    typed = StoredFinding.of_model_output(raw, adjudication)
    assert typed.body.adjudication is adjudication
    assert stored_finding(typed.to_json()).to_json() == typed.to_json()
    with pytest.raises(ValueError, match="adjudication"):
        StoredFinding.of_model_output({**raw, "adjudication": None}, adjudication)
