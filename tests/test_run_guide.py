import json
from pathlib import Path

import pytest
from review_fakes import make_review

from jigane.commands import main
from jigane.commands.run_guide import _SHARD_GUIDE_TEXT, RunGuide, run_guide
from jigane.groups.definitions import GroupId
from jigane.reviews.lifecycle import PullRequestInfo, Review, create_feature_review
from jigane.skills.shards import ShardPlanKind, plan_kind_for
from jigane.storage.store import ReviewKind

_SHARDED_GROUPS = (GroupId.MICRO, GroupId.SECURITY)


def _review(tmp_path: Path, kind: ReviewKind) -> Review:
    pull_request = PullRequestInfo("u", 7, "t") if kind is ReviewKind.PULL_REQUEST else None
    base_tree = None if kind is ReviewKind.FEATURE else tmp_path
    return make_review(tmp_path, [], kind=kind, base_tree=base_tree, pull_request=pull_request)


# 出所 -> 群 -> 計画の種類。案内の文はこの計画の種類で決まる
_EXPECTED_PLAN = {
    (ReviewKind.PULL_REQUEST, GroupId.MICRO): ShardPlanKind.DIFF,
    (ReviewKind.PULL_REQUEST, GroupId.SECURITY): ShardPlanKind.DIFF,
    (ReviewKind.RANGE, GroupId.MICRO): ShardPlanKind.DIFF,
    (ReviewKind.RANGE, GroupId.SECURITY): ShardPlanKind.DIFF,
    (ReviewKind.FEATURE, GroupId.MICRO): ShardPlanKind.FILES,
    (ReviewKind.FEATURE, GroupId.SECURITY): ShardPlanKind.FILES,
    (ReviewKind.DESIGN_DOC, GroupId.MICRO): ShardPlanKind.DESIGN,
    (ReviewKind.DESIGN_DOC, GroupId.SECURITY): ShardPlanKind.DESIGN,
}


@pytest.mark.parametrize(("kind", "group"), list(_EXPECTED_PLAN))
def test_guide_text_follows_the_plan_kind_of_the_run(
    tmp_path: Path, kind: ReviewKind, group: GroupId
) -> None:
    review = _review(tmp_path, kind)
    plan_kind = plan_kind_for(group, review)
    assert plan_kind is _EXPECTED_PLAN[(kind, group)]
    text = run_guide(review, group).text
    match plan_kind:
        case ShardPlanKind.DIFF:
            assert "差分" in text
        case ShardPlanKind.FILES:
            assert "選んだファイルの本文" in text
            assert "差分" not in text
        case ShardPlanKind.DESIGN:
            assert "設計書のシャード" in text
            assert "ファイルのシャード" in text


def test_same_plan_kind_gives_the_same_text(tmp_path: Path) -> None:
    pull_request = run_guide(_review(tmp_path, ReviewKind.PULL_REQUEST), GroupId.MICRO)
    range_ = run_guide(_review(tmp_path, ReviewKind.RANGE), GroupId.MICRO)
    assert pull_request.text == range_.text
    assert pull_request.review_kind is not range_.review_kind


def test_feature_guide_does_not_speak_of_diffs(tmp_path: Path) -> None:
    review = _review(tmp_path, ReviewKind.FEATURE)
    for group in _SHARDED_GROUPS:
        assert "差分" not in run_guide(review, group).text


def test_design_doc_security_asks_design_and_file_shards(tmp_path: Path) -> None:
    text = run_guide(_review(tmp_path, ReviewKind.DESIGN_DOC), GroupId.SECURITY).text
    assert "設計書のシャード" in text
    assert "ファイルのシャード" in text
    assert "セキュリティ上の懸念" in text


def test_guide_table_covers_every_plan_kind_and_sharded_group() -> None:
    expected = {
        (kind, group) for kind in ShardPlanKind for group in GroupId if group is not GroupId.MACRO
    }
    assert set(_SHARD_GUIDE_TEXT) == expected


@pytest.mark.parametrize("kind", list(ReviewKind))
def test_every_origin_and_group_has_a_guide(tmp_path: Path, kind: ReviewKind) -> None:
    review = _review(tmp_path, kind)
    for group in GroupId:
        guide = run_guide(review, group)
        assert guide.review_kind is kind
        assert guide.text


def test_to_json_is_the_only_place_of_the_keys() -> None:
    guide = RunGuide(ReviewKind.DESIGN_DOC, "文")
    assert guide.to_json() == {"review_kind": "design_doc", "text": "文"}
    with pytest.raises(AttributeError):
        guide.text = "別"  # type: ignore[misc]


def test_estimate_json_carries_run_guide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    review = create_feature_review(root, ["src"], "p")
    main(["estimate", str(review.id), "security", "--json"])
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["run_guide"] == run_guide(review, GroupId.SECURITY).to_json()
    assert estimate["run_guide"]["review_kind"] == "feature"
