"""テストで偽のレビューを組み立てる補助。

配布物（wheel）には入らない。jigane のテストと、jigane の開発のリポジトリの運用のテストだけが使う。
"""

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, assert_never, cast

from jigane.findings.records import (
    GroupResult,
    HistoryRound,
    StoredFinding,
    ThemeUpdate,
    _upgrade_finding_v1,
    _upgrade_finding_v2,
    fill_triage,
    load_history_checked,
)
from jigane.gates.entry import FlakyDetail, GateEntry, GateSeverity
from jigane.groups.definitions import MACRO, GroupId
from jigane.judgment_map.classification import ChangeKind, Segment
from jigane.judgment_map.declaration import Declaration, DeclarationSource
from jigane.judgment_map.kinds import LEVEL_OF_KIND, Kind
from jigane.judgment_map.map_kind import MAP_FILE, MAP_FORMAT, dump_map, load_map
from jigane.judgment_map.region_table import LineCounts, Region, RegionTable, region_from_dict
from jigane.languages.base import Span
from jigane.pull_request_context.bots import BotAnalysis
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import (
    Content,
    DesignDocContent,
    DesignDocOrigin,
    DiffSource,
    FeatureOrigin,
    LocalRange,
    PullRequestContent,
    PullRequestInfo,
    PullRequestOrigin,
    RangeOrigin,
    Review,
    ReviewContent,
    ReviewOrigin,
)
from jigane.storage.store import ReviewId, ReviewKind

# gates だけが変わったレビューを作るための、実在の検出に当たらない警告
EXTRA_WARNING = GateEntry(
    "W99",
    "FLK-EXTRA",
    "tests/test_extra.py",
    1,
    "x",
    {},
    GateSeverity.FLAKY,
    FlakyDetail("", "c", "direct", "o", ""),
)


def _check_review_arguments(
    kind: ReviewKind, base_tree: Path | None, pull_request: PullRequestInfo | None
) -> None:
    """make_review の出所の引数が、種類に要る組み合わせで揃っているかを確かめる。"""
    if kind is ReviewKind.FEATURE:
        if base_tree is not None or pull_request is not None:
            raise ValueError("FEATURE の Review は base_tree も pull_request も持たない")
    elif base_tree is None:
        raise ValueError(f"{kind.name} の Review には base_tree が要る")
    elif (kind is ReviewKind.PULL_REQUEST) != (pull_request is not None):
        raise ValueError("pull_request は PULL_REQUEST の Review にだけ渡す")


def _origin_for(
    kind: ReviewKind,
    repository_root: Path,
    base_tree: Path | None,
    pull_request: PullRequestInfo | None,
) -> ReviewOrigin:
    """種類に合う出所。引数の組み合わせは _check_review_arguments が確かめ済みの前提で組む。"""
    source = (
        DiffSource("b" * 40, "h" * 40, "m" * 40, str(base_tree)) if base_tree is not None else None
    )
    match kind:
        case ReviewKind.FEATURE:
            return FeatureOrigin()
        case ReviewKind.PULL_REQUEST:
            return PullRequestOrigin(
                cast("PullRequestInfo", pull_request), cast("DiffSource", source)
            )
        case ReviewKind.RANGE:
            return RangeOrigin(
                LocalRange("b..h", str(repository_root)), cast("DiffSource", source)
            )
        case ReviewKind.DESIGN_DOC:
            return DesignDocOrigin("design.md", "d" * 64, "c" * 40, ())
        case _:
            assert_never(kind)


def make_review(
    repository_root: Path,
    paths: list[str],
    *,
    kind: ReviewKind,
    base_tree: Path | None = None,
    pull_request: PullRequestInfo | None = None,
    judgment_map: dict[str, Any] | None = None,
) -> Review:
    """保存せずに組み立てた Review。種類ごとに要る出所の引数が揃わなければ ValueError にする。"""
    _check_review_arguments(kind, base_tree, pull_request)
    origin = _origin_for(kind, repository_root, base_tree, pull_request)
    key = "7" if kind is ReviewKind.PULL_REQUEST else "scratch"
    return Review(
        ReviewId("octo", "hello", key, kind),
        "2026-01-01T00:00:00+00:00",
        str(repository_root),
        paths,
        Declaration(DeclarationSource.INTENT, "x", ()),
        [],
        load_map(
            {
                "format": MAP_FORMAT,
                "mode": "feature",
                **_complete_map(judgment_map or {}),
            }
        ),
        origin,
    )


def build_content(project: Path, kind: ReviewKind) -> Content:
    """種類に合う出所を持つ保存用の内容。範囲と PR の出所は project の隣の base を指す。"""
    feature_content = review_module._build_feature_content(project, [project / "src"], None)
    base = project.parent / "base"
    base.mkdir(exist_ok=True)
    source = DiffSource("b" * 40, "h" * 40, "m" * 40, str(base))
    origin: ReviewOrigin
    match kind:
        case ReviewKind.FEATURE:
            origin = FeatureOrigin()
        case ReviewKind.PULL_REQUEST:
            origin = PullRequestOrigin(PullRequestInfo("u", 7, "t"), source)
        case ReviewKind.RANGE:
            origin = RangeOrigin(LocalRange("b..h", str(project)), source)
        case ReviewKind.DESIGN_DOC:
            origin = DesignDocOrigin("design.md", "d" * 64, "c" * 40, ("src",))
    head = (
        feature_content.paths,
        feature_content.declaration,
        feature_content.content_warnings,
        feature_content.judgment_map,
    )
    if isinstance(origin, PullRequestOrigin):
        return PullRequestContent(*head, origin=origin, bot_analysis=BotAnalysis())
    if isinstance(origin, DesignDocOrigin):
        empty_table = RegionTable([], LineCounts.empty())
        return DesignDocContent(*head, origin=origin, section_map=empty_table)
    return ReviewContent(*head, origin=origin)


def save_review_of_kind(project: Path, key: str, kind: ReviewKind) -> ReviewId:
    """種類に合う出所の内容で、`local/shop` の下に指定の種類とキーのレビューを保存する。"""
    review_id = ReviewId("local", "shop", key, kind)
    review_module.save_review(review_id, build_content(project, kind), "t", None, str(project))
    return review_id


def with_range_source(review: Review, base_tree: Path) -> Review:
    """既存の Review を、範囲の出所（DiffSource と LocalRange）を持つ差分レビューに置き換える。"""
    diff = DiffSource("b" * 40, "h" * 40, "m" * 40, str(base_tree))
    return replace(review, origin=RangeOrigin(LocalRange("b..h", review.repository_root), diff))


def write_fake_review(
    directory: Path, repository_root: Path, kind: ReviewKind = ReviewKind.FEATURE
) -> Review:
    """directory に review.json と map.json だけの偽のレビューを書く。保存先は識別子と合わない。"""
    base_tree = None if kind is ReviewKind.FEATURE else repository_root
    review = make_review(repository_root, [], kind=kind, base_tree=base_tree)
    directory.mkdir(parents=True, exist_ok=True)
    review_module._write_review_file(directory, review)
    (directory / MAP_FILE).write_text(json.dumps(dump_map(review.map_kind)), encoding="utf-8")
    return review


def history_rounds(directory: Path, group: GroupId) -> list[HistoryRound]:
    """群の履歴の回を新しい回から順に返す。壊れていれば空。"""
    return list(reversed(load_history_checked(directory).rounds.get(group, [])))


def stored_finding(raw: dict[str, Any]) -> StoredFinding:
    """欠けた鍵のある辞書を、版 1 の指摘として今の版まで移してから読む。"""
    return StoredFinding.from_json(_upgrade_finding_v2(_upgrade_finding_v1(raw)))


def group_result(
    findings: list[dict[str, Any]], theme_update: ThemeUpdate | None = None
) -> GroupResult:
    """保存の形の辞書から、確定後の指摘の結果を作る。区分は辞書の値を使う。"""
    return GroupResult(
        tuple(fill_triage(MACRO, stored_finding(f)) for f in findings), theme_update
    )


def region_of(row: Mapping[str, Any]) -> Region:
    """試験が要る項目だけの行から、足りない項目を既定で埋めた Region を作る。"""
    kind = row.get("kind", "public_interface")
    filled: dict[str, Any] = {
        "key": f"{row['path']}::{row.get('qualified_name', '')}::{kind}",
        "kind": kind,
        "qualified_name": "",
        "summary": "",
        "old_span": None,
        "new_span": None,
        "line_count": 0,
        "content_hash": "",
        "bot_comment_ids": [],
        "old_path": None,
    }
    return region_from_dict({**filled, **row})


def changed_segment(
    path: str,
    start_line: int,
    end_line: int,
    qualified_name: str,
    declaration_kind: str | None = "method",
) -> Segment:
    """変更後のパスと範囲だけを指定した区間。足りない項目は、論理の変更の既定で埋める。"""
    span = Span(start_line, end_line)
    lines = end_line - start_line + 1
    return Segment(
        old_path=path,
        new_path=path,
        old_span=span,
        new_span=span,
        qualified_name=qualified_name,
        kind=Kind.LOGIC,
        level=LEVEL_OF_KIND[Kind.LOGIC],
        old_line_count=lines,
        new_line_count=lines,
        declaration_kind=declaration_kind,
        change_kind=ChangeKind.CHANGED,
    )


def _complete_map(map_dict: Mapping[str, Any]) -> dict[str, Any]:
    """試験が書いた項目だけの地図の dict を、map.json の形が要る項目まで埋める。"""
    return {
        **map_dict,
        "line_counts": {"by_kind": {}, "by_level": {}, **map_dict.get("line_counts", {})},
        "segments": [_segment_row(row) for row in map_dict.get("segments", [])],
        "regions": [region_of(row).to_dict() for row in map_dict.get("regions", [])],
    }


def _segment_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """試験が要る項目だけの区間の行を、Segment が読み戻せる項目まで既定で埋める。"""
    kind = Kind(row.get("kind", Kind.LOGIC))
    filled: dict[str, Any] = {
        "old_path": None,
        "new_path": None,
        "old_span": None,
        "new_span": None,
        "qualified_name": "",
        "kind": kind.value,
        "level": LEVEL_OF_KIND[kind].value,
        "old_line_count": 0,
        "new_line_count": 0,
        "declaration_kind": None,
        "change_kind": ChangeKind.CHANGED.value,
        "bot_comment_ids": [],
        "visibility": None,
        "static_finding_ids": [],
    }
    return {**filled, **row}


def with_map(review: Review, map_dict: dict[str, Any]) -> Review:
    """既存の Review の地図を、map.json の形の dict から復号した値に置き換える。

    map_dict に mode が無ければ、元の Review の地図の mode を引き継ぐ。
    """
    mode = review.map_kind.to_dict()["mode"]
    return replace(
        review, map_kind=load_map({"format": MAP_FORMAT, "mode": mode, **_complete_map(map_dict)})
    )
