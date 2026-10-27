"""識別子の種類と保存した出所の食い違いの検査を、読み込みと一覧に通す。"""

import json
import shutil
from collections.abc import Callable
from typing import Any

import pytest
from pr_support import URL

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import (
    REVIEW_FILE,
    ReviewError,
    ReviewKindMismatchError,
    create_pull_request_review,
    list_reviews,
    load_review,
)
from jigane.storage.store import ReviewId, ReviewKind


def _relocate(
    source: ReviewId, target: ReviewId, mutate: Callable[[dict[str, Any]], None] | None = None
) -> None:
    """source の記録を target の保存先へ写し、review.json の id と中身を書き換える。"""
    shutil.copytree(source.directory(), target.directory())
    path = target.directory() / REVIEW_FILE
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["id"] = str(target)
    if mutate is not None:
        mutate(meta)
    path.write_text(json.dumps(meta), encoding="utf-8")


def _drop_pull_request(meta: dict[str, Any]) -> None:
    # 差分の出所だけが残る。範囲のレビューの形
    meta["pull_request"] = None


def _as_range(meta: dict[str, Any]) -> None:
    # 範囲のレビューの形。PR の情報を外し、範囲の情報を足す
    meta["pull_request"] = None
    meta["local_range"] = {"range_text": "a..b", "working_directory": "/work"}


def _drop_sources(meta: dict[str, Any]) -> None:
    meta["pull_request"] = None
    meta["diff_source"] = None


@pytest.mark.usefixtures("pr")
def test_each_kind_loads_when_the_stored_source_agrees() -> None:
    pull_request = create_pull_request_review(URL).id
    owner, repo = pull_request.owner, pull_request.repo
    range_id = ReviewId(owner, repo, "feat", ReviewKind.RANGE)
    feature_id = ReviewId(owner, repo, "feat", ReviewKind.FEATURE)
    _relocate(pull_request, range_id, _as_range)
    _relocate(pull_request, feature_id, _drop_sources)
    for review_id in (pull_request, range_id, feature_id):
        assert load_review(review_id).kind == review_id.kind


@pytest.mark.usefixtures("pr")
def test_load_stops_when_the_pull_request_slot_holds_another_source() -> None:
    pull_request = create_pull_request_review(URL).id
    elsewhere = ReviewId(pull_request.owner, pull_request.repo, "9", ReviewKind.PULL_REQUEST)
    _relocate(pull_request, elsewhere, _drop_pull_request)
    with pytest.raises(ReviewKindMismatchError, match="食い違います"):
        load_review(elsewhere)


@pytest.mark.parametrize(
    ("kind", "mutate"),
    [
        (ReviewKind.RANGE, None),
        (ReviewKind.RANGE, _drop_sources),
        (ReviewKind.FEATURE, None),
        (ReviewKind.FEATURE, _drop_pull_request),
    ],
)
@pytest.mark.usefixtures("pr")
def test_load_stops_when_the_slot_kind_differs_from_the_stored_source(
    kind: ReviewKind, mutate: Callable[[dict[str, Any]], None] | None
) -> None:
    pull_request = create_pull_request_review(URL).id
    target = ReviewId(pull_request.owner, pull_request.repo, "x", kind)
    _relocate(pull_request, target, mutate)
    with pytest.raises(ReviewError, match="食い違います"):
        load_review(target)


@pytest.mark.usefixtures("pr")
def test_list_skips_a_mismatched_record_and_warns() -> None:
    pull_request = create_pull_request_review(URL).id
    bad = ReviewId(pull_request.owner, pull_request.repo, "9", ReviewKind.PULL_REQUEST)
    _relocate(pull_request, bad, _drop_pull_request)
    with pytest.warns(UserWarning, match=str(bad)):
        listed = list_reviews()
    assert [review.id for review in listed] == [pull_request]
    with pytest.raises(ReviewError):
        load_review(bad)


@pytest.mark.usefixtures("pr")
def test_list_json_rows_carry_the_kind(capsys: pytest.CaptureFixture[str]) -> None:
    pull_request = create_pull_request_review(URL).id
    feature_id = ReviewId(pull_request.owner, pull_request.repo, "feat", ReviewKind.FEATURE)
    _relocate(pull_request, feature_id, _drop_sources)
    capsys.readouterr()
    assert main(["list", "--json"]) == EXIT_OK
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)}
    repository = f"{pull_request.owner}/{pull_request.repo}"
    assert rows[str(pull_request)]["kind"] == "pull_request"
    assert rows[str(pull_request)]["is_pull_request"] is True
    assert rows[str(feature_id)]["kind"] == "feature"
    assert rows[str(feature_id)]["is_pull_request"] is False
    assert rows[str(feature_id)]["repository"] == repository
    assert rows[str(feature_id)]["key"] == "feat"
