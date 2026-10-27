from pathlib import Path

import pytest
from review_fakes import make_review

from jigane.reviews.lifecycle import (
    DesignDocOrigin,
    FeatureOrigin,
    LocalRange,
    PullRequestInfo,
    PullRequestOrigin,
    RangeOrigin,
)
from jigane.storage.store import ReviewKind

_PULL_REQUEST = PullRequestInfo("u", 7, "t")


def test_feature_review_takes_no_source_arguments(tmp_path: Path) -> None:
    review = make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE)
    assert review.origin == FeatureOrigin()
    assert review.id.key == "scratch"
    with pytest.raises(ValueError, match="FEATURE"):
        make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE, base_tree=tmp_path)
    with pytest.raises(ValueError, match="FEATURE"):
        make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE, pull_request=_PULL_REQUEST)


@pytest.mark.parametrize(
    "kind", [ReviewKind.PULL_REQUEST, ReviewKind.RANGE, ReviewKind.DESIGN_DOC]
)
def test_other_kinds_need_a_base_tree(tmp_path: Path, kind: ReviewKind) -> None:
    with pytest.raises(ValueError, match=f"{kind.name} の Review には base_tree が要る"):
        make_review(tmp_path, ["a.py"], kind=kind)


def test_pull_request_review_needs_the_pull_request_information(tmp_path: Path) -> None:
    review = make_review(
        tmp_path,
        ["a.py"],
        kind=ReviewKind.PULL_REQUEST,
        base_tree=tmp_path,
        pull_request=_PULL_REQUEST,
    )
    assert isinstance(review.origin, PullRequestOrigin)
    assert review.id.key == "7"
    with pytest.raises(ValueError, match="pull_request は PULL_REQUEST"):
        make_review(tmp_path, ["a.py"], kind=ReviewKind.PULL_REQUEST, base_tree=tmp_path)


@pytest.mark.parametrize("kind", [ReviewKind.RANGE, ReviewKind.DESIGN_DOC])
def test_pull_request_argument_is_only_for_pull_request_reviews(
    tmp_path: Path, kind: ReviewKind
) -> None:
    with pytest.raises(ValueError, match="pull_request は PULL_REQUEST"):
        make_review(tmp_path, ["a.py"], kind=kind, base_tree=tmp_path, pull_request=_PULL_REQUEST)


def test_range_and_design_doc_reviews_build_their_origins(tmp_path: Path) -> None:
    range_review = make_review(tmp_path, ["a.py"], kind=ReviewKind.RANGE, base_tree=tmp_path)
    assert isinstance(range_review.origin, RangeOrigin)
    assert range_review.origin.range == LocalRange("b..h", str(tmp_path))
    design_review = make_review(tmp_path, ["a.py"], kind=ReviewKind.DESIGN_DOC, base_tree=tmp_path)
    assert isinstance(design_review.origin, DesignDocOrigin)
    assert design_review.id.key == "scratch"
