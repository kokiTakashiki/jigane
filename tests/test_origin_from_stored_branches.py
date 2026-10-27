from typing import Any

import pytest

from jigane.reviews.lifecycle import (
    DesignDocOrigin,
    DiffSource,
    FeatureOrigin,
    LocalRange,
    PullRequestInfo,
    PullRequestOrigin,
    RangeOrigin,
    ReviewError,
    _origin_from_stored,
)
from jigane.storage.store import ReviewKind

_PULL_REQUEST = PullRequestInfo("u", 7, "t")
_RANGE = LocalRange("b..h", "/repo")
_SOURCE = DiffSource("b" * 40, "h" * 40, "m" * 40, "/base")
_DESIGN: dict[str, Any] = DesignDocOrigin("d.md", "v", "c", ()).to_dict()


def test_pull_request_kind_builds_its_origin() -> None:
    assert _origin_from_stored(
        ReviewKind.PULL_REQUEST, _PULL_REQUEST, None, _SOURCE, None, design_doc=None
    ) == PullRequestOrigin(_PULL_REQUEST, _SOURCE)


@pytest.mark.parametrize(
    ("pull_request", "diff_source"), [(None, _SOURCE), (_PULL_REQUEST, None), (None, None)]
)
def test_pull_request_kind_needs_both_items(
    pull_request: PullRequestInfo | None, diff_source: DiffSource | None
) -> None:
    with pytest.raises(ReviewError, match="PR の記録に、PR の情報か差分の出所がありません"):
        _origin_from_stored(
            ReviewKind.PULL_REQUEST, pull_request, None, diff_source, None, design_doc=None
        )


def test_pull_request_kind_rejects_a_foreign_item() -> None:
    with pytest.raises(ReviewError, match="別の種類の項目（範囲の情報）"):
        _origin_from_stored(
            ReviewKind.PULL_REQUEST, _PULL_REQUEST, _RANGE, _SOURCE, None, design_doc=None
        )


def test_range_kind_builds_its_origin() -> None:
    assert _origin_from_stored(
        ReviewKind.RANGE, None, _RANGE, _SOURCE, None, design_doc=None
    ) == RangeOrigin(_RANGE, _SOURCE)


@pytest.mark.parametrize(("local_range", "diff_source"), [(None, _SOURCE), (_RANGE, None)])
def test_range_kind_needs_both_items(
    local_range: LocalRange | None, diff_source: DiffSource | None
) -> None:
    with pytest.raises(ReviewError, match="範囲の記録に、範囲の情報か差分の出所がありません"):
        _origin_from_stored(
            ReviewKind.RANGE, None, local_range, diff_source, None, design_doc=None
        )


def test_range_kind_rejects_a_foreign_item() -> None:
    with pytest.raises(ReviewError, match="別の種類の項目（PR の情報）"):
        _origin_from_stored(
            ReviewKind.RANGE, _PULL_REQUEST, _RANGE, _SOURCE, None, design_doc=None
        )


def test_feature_kind_keeps_targets_as_a_tuple_or_none() -> None:
    assert _origin_from_stored(
        ReviewKind.FEATURE, None, None, None, ["a", "b"], design_doc=None
    ) == FeatureOrigin(("a", "b"))
    assert _origin_from_stored(
        ReviewKind.FEATURE, None, None, None, None, design_doc=None
    ) == FeatureOrigin(None)


def test_feature_kind_rejects_every_foreign_item() -> None:
    with pytest.raises(ReviewError, match="別の種類の項目（差分の出所）"):
        _origin_from_stored(ReviewKind.FEATURE, None, None, _SOURCE, None, design_doc=None)
    with pytest.raises(ReviewError, match="別の種類の項目（設計書の情報）"):
        _origin_from_stored(ReviewKind.FEATURE, None, None, None, None, design_doc=_DESIGN)


def test_design_doc_kind_builds_its_origin() -> None:
    assert _origin_from_stored(
        ReviewKind.DESIGN_DOC, None, None, None, None, design_doc=_DESIGN
    ) == DesignDocOrigin("d.md", "v", "c", ())


def test_design_doc_kind_rejects_a_foreign_item() -> None:
    with pytest.raises(ReviewError, match="別の種類の項目（差分の出所）"):
        _origin_from_stored(ReviewKind.DESIGN_DOC, None, None, _SOURCE, None, design_doc=_DESIGN)
