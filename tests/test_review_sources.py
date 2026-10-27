import json
import shutil
from pathlib import Path

import pytest
from review_fakes import make_review

from jigane.commands import generation
from jigane.commands.views.review import source_lines
from jigane.findings.discard import ShardConcerns
from jigane.groups.definitions import GroupId
from jigane.judgment_map.classification import build_judgment_map
from jigane.judgment_map.configuration import Config
from jigane.judgment_map.map_kind import MAP_FILE, dump_map
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import (
    FeatureOrigin,
    PullRequestInfo,
    PullRequestOrigin,
    RangeOrigin,
    ReviewError,
    load_review,
    read_stored_sources,
)
from jigane.skills import macro, sharded, shards
from jigane.skills.assembly import AssemblyInput
from jigane.storage.store import ReviewKind

INFO = PullRequestInfo("https://github.com/octo/hello/pull/7", 7, "支払い")


def _round_trip(review: review_module.Review) -> review_module.Review:
    directory = review.id.directory()
    directory.mkdir(parents=True)
    review_module._write_review_file(directory, review)
    (directory / MAP_FILE).write_text(json.dumps(dump_map(review.map_kind)), encoding="utf-8")
    return load_review(review.id)


def test_feature_review_round_trips(tmp_path: Path) -> None:
    loaded = _round_trip(make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE))
    assert loaded.origin == FeatureOrigin()
    assert not loaded.is_pull_request
    assert loaded.base_tree_path is None


def test_pull_request_diff_review_round_trips(tmp_path: Path) -> None:
    original = make_review(
        tmp_path,
        ["a.py"],
        base_tree=tmp_path / "base",
        pull_request=INFO,
        kind=ReviewKind.PULL_REQUEST,
    )
    loaded = _round_trip(original)
    assert loaded.origin == original.origin
    assert loaded.origin == PullRequestOrigin(INFO, original.diff_origin)  # type: ignore[arg-type]
    assert loaded.is_pull_request
    assert loaded.base_tree_path == tmp_path / "base"


def test_diff_review_without_pull_request_round_trips(tmp_path: Path) -> None:
    original = make_review(tmp_path, ["a.py"], base_tree=tmp_path / "base", kind=ReviewKind.RANGE)
    loaded = _round_trip(original)
    assert loaded.origin == original.origin
    assert isinstance(loaded.origin, RangeOrigin)
    assert not loaded.is_pull_request
    assert loaded.base_tree_path == tmp_path / "base"


def test_legacy_pull_request_value_is_split_by_the_upgrade() -> None:
    legacy = {
        **INFO.to_dict(),
        "linked_issues": [],
        "base_sha": "b",
        "head_sha": "h",
        "merge_base": "m",
        "base_tree": "/t",
    }
    raw = {
        "id": "owner/repo#1",
        "declaration": {"source": "pull_request", "intent": None, "excerpts": []},
        "warnings": [],
        "pull_request": legacy,
    }
    upgraded = review_module._REVIEW_UPGRADES[1](raw)
    source, info = read_stored_sources(upgraded["pull_request"], upgraded["diff_source"])
    assert source is not None
    assert source.base_tree == "/t"
    assert info == INFO


def test_header_lines_are_split_by_source(tmp_path: Path) -> None:
    pull_request = make_review(
        tmp_path,
        ["a.py"],
        base_tree=tmp_path / "base",
        pull_request=INFO,
        kind=ReviewKind.PULL_REQUEST,
    )
    assert source_lines(pull_request) == [
        "PR: 支払い",
        f"  {INFO.url}",
        "  merge-base mmmmmmm、head hhhhhhh",
    ]
    range_review = make_review(
        tmp_path, ["a.py"], base_tree=tmp_path / "base", kind=ReviewKind.RANGE
    )
    assert source_lines(range_review) == [
        "範囲: b..h",
        "  merge-base mmmmmmm、head hhhhhhh",
    ]
    assert source_lines(make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE)) == []


@pytest.mark.parametrize("with_base", [False, True])
def test_overview_json_and_review_point_at_the_same_base_tree(
    tmp_path: Path, *, with_base: bool
) -> None:
    review = make_review(
        tmp_path,
        ["a.py"],
        base_tree=tmp_path / "base" if with_base else None,
        kind=ReviewKind.RANGE if with_base else ReviewKind.FEATURE,
    )
    overview = json.loads(json.dumps(review.to_dict()))
    stored = overview["diff_source"]
    assert (
        review_module.DiffSource.path_of(stored["base_tree"]) if stored else None
    ) == review.base_tree_path
    assert (review.base_tree_path is not None) == with_base


def test_mode_is_derived_and_a_conflicting_stored_value_is_ignored(tmp_path: Path) -> None:
    for original, expected in (
        (make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE), "feature"),
        (
            make_review(tmp_path, ["a.py"], base_tree=tmp_path / "base", kind=ReviewKind.RANGE),
            "diff",
        ),
    ):
        assert original.mode == expected
        loaded = _round_trip(original)
        meta_path = loaded.id.directory() / review_module.REVIEW_FILE
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
        assert "mode" not in stored
        # 版 3 の review.json は format が 3 で、導ける項目の mode を食い違う値で持つ
        v3 = {
            **stored,
            "format": 3,
            "kind": str(original.kind),
            "mode": "feature" if expected == "diff" else "diff",
            "is_diff": original.mode != "diff",
            "is_pull_request": False,
        }
        meta_path.write_text(json.dumps(v3), encoding="utf-8")
        # 版 3 から 4 への移しが mode を捨てるので、読み戻しは差分の出所の有無が決める
        assert load_review(loaded.id).mode == expected
        shutil.rmtree(loaded.id.directory())


def test_judgment_map_mode_agrees_with_review_mode(tmp_path: Path) -> None:
    for base in (None, tmp_path / "base"):
        review = make_review(
            tmp_path,
            [],
            base_tree=base,
            kind=ReviewKind.RANGE if base is not None else ReviewKind.FEATURE,
        )
        built = build_judgment_map(base, tmp_path, [], Config())
        assert built.mode == review.mode


@pytest.mark.parametrize(
    ("review_kind", "base", "pull_request", "is_diff", "is_pull_request"),
    [
        (ReviewKind.FEATURE, False, None, False, False),
        (ReviewKind.RANGE, True, None, True, False),
        (ReviewKind.PULL_REQUEST, True, INFO, True, True),
    ],
)
def test_consumers_read_the_three_source_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    review_kind: ReviewKind,
    *,
    base: bool,
    pull_request: PullRequestInfo | None,
    is_diff: bool,
    is_pull_request: bool,
) -> None:
    review = make_review(
        tmp_path,
        ["a.py"],
        base_tree=tmp_path / "base" if base else None,
        pull_request=pull_request,
        kind=review_kind,
    )
    directory = review.id.directory()
    directory.mkdir(parents=True)
    (directory / "changes.diff").write_text("-removed_line\n+kept_line\n", encoding="utf-8")
    assert (review.is_diff, review.is_pull_request) == (is_diff, is_pull_request)
    assert ("kept_line" in macro.changes_prompt_text(review)) == is_diff
    assert ("機能レビュー式では背景の材料を集めない" in macro.background_prompt_text(review)) == (
        not is_diff
    )
    monkeypatch.setattr(generation, "resolve_review", lambda _name: review.id)
    monkeypatch.setattr(generation, "load_review", lambda _id: review)
    if is_diff:
        # PR と範囲のレビューは差分レビュー式なので、export-reply が文を作れる
        return
    with pytest.raises(ReviewError, match="差分レビュー式"):
        generation.generate("unused", "default", None, generation.ExportKind.REPLY, as_json=False)


@pytest.mark.parametrize(
    ("review_kind", "base", "pull_request", "kind"),
    [
        (ReviewKind.FEATURE, False, None, "feature"),
        (ReviewKind.RANGE, True, None, "range"),
        (ReviewKind.PULL_REQUEST, True, INFO, "pull_request"),
    ],
)
def test_to_dict_carries_the_derived_source_values(
    tmp_path: Path,
    review_kind: ReviewKind,
    *,
    base: bool,
    pull_request: PullRequestInfo | None,
    kind: str,
) -> None:
    review = make_review(
        tmp_path,
        ["a.py"],
        base_tree=tmp_path / "base" if base else None,
        pull_request=pull_request,
        kind=review_kind,
    )
    overview = review.to_dict()
    assert (overview["is_diff"], overview["is_pull_request"], overview["kind"]) == (
        base,
        pull_request is not None,
        kind,
    )
    assert overview["mode"] == ("diff" if base else "feature")
    # 判断の地図の mode は、構築の側が出所から別に引くため、Review の導出値と突き合わせる
    built = build_judgment_map(tmp_path / "base" if base else None, tmp_path, [], Config())
    assert built.mode == overview["mode"]


@pytest.mark.parametrize(
    ("review_kind", "base", "pull_request"),
    [
        (ReviewKind.FEATURE, False, None),
        (ReviewKind.RANGE, True, None),
        (ReviewKind.PULL_REQUEST, True, INFO),
    ],
    ids=["feature", "range", "pr"],
)
def test_shards_are_built_from_the_three_source_shapes(
    tmp_path: Path, review_kind: ReviewKind, *, base: bool, pull_request: PullRequestInfo | None
) -> None:
    root, base_tree = tmp_path / "head", tmp_path / "base"
    root.mkdir()
    base_tree.mkdir()
    (base_tree / "edit.py").write_text("old_line\n", encoding="utf-8")
    (root / "edit.py").write_text("new_line\n", encoding="utf-8")
    review = make_review(
        root,
        ["edit.py"],
        base_tree=base_tree if base else None,
        pull_request=pull_request,
        kind=review_kind,
    )
    directory = review.id.directory()
    directory.mkdir(parents=True)
    (directory / review_module.PAIRS_FILE).write_text(
        json.dumps([["edit.py", "edit.py"]]), encoding="utf-8"
    )
    units = shards.build_units(review)
    assert [unit.path for unit in units] == ["edit.py"]
    # 差分レビュー式は変更前後の差分、機能レビュー式は本文そのものを単位にする
    assert ("-old_line" in units[0].text) == base
    assert ("=== edit.py ===" in units[0].text) == (not base)
    [shard] = shards.build_plan(review, GroupId.MICRO).shards
    prompt = sharded.build_shard_prompt(
        GroupId.MICRO, AssemblyInput.load(review), shard, 1, 1, concerns=ShardConcerns()
    )
    assert "- edit.py" in prompt
    assert (sharded.DIFF_INTRO in prompt) == base
    assert (sharded.FEATURE_INTRO in prompt) == (not base)


def test_stored_fields_keep_the_shape_of_each_origin(tmp_path: Path) -> None:
    feature = make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE).to_dict()
    assert {k: feature[k] for k in ("targets", "diff_source", "pull_request", "local_range")} == {
        "targets": None,
        "diff_source": None,
        "pull_request": None,
        "local_range": None,
    }
    pull_request = make_review(
        tmp_path,
        ["a.py"],
        base_tree=tmp_path / "base",
        pull_request=INFO,
        kind=ReviewKind.PULL_REQUEST,
    ).to_dict()
    assert pull_request["pull_request"] == INFO.to_dict()
    assert pull_request["local_range"] is None
    assert set(pull_request["diff_source"]) == {"base_sha", "head_sha", "merge_base", "base_tree"}
    range_review = make_review(
        tmp_path, ["a.py"], base_tree=tmp_path / "base", kind=ReviewKind.RANGE
    ).to_dict()
    assert range_review["pull_request"] is None
    assert set(range_review["local_range"]) == {"range_text", "working_directory"}
    assert range_review["diff_source"]["base_tree"] == str(tmp_path / "base")


def test_inconsistent_stored_fields_are_a_review_error(tmp_path: Path) -> None:
    review = make_review(
        tmp_path,
        ["a.py"],
        base_tree=tmp_path / "base",
        pull_request=INFO,
        kind=ReviewKind.PULL_REQUEST,
    )
    directory = tmp_path / "stored"
    directory.mkdir()
    review_module._write_review_file(directory, review)
    (directory / MAP_FILE).write_text(json.dumps(dump_map(review.map_kind)), encoding="utf-8")
    path = directory / review_module.REVIEW_FILE
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["diff_source"] = None
    meta["pull_request"] = {**meta["pull_request"]}
    path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ReviewError):
        review_module.read_review_at(directory)
