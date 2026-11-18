import json
from pathlib import Path
from typing import Any

import pytest
from listing_support import listing_warnings
from test_review_relations import save

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.judgment_map import map_kind
from jigane.reviews import catalog
from jigane.reviews.catalog import list_review_summaries, list_reviews
from jigane.storage.review_error import ReviewError
from jigane.storage.store import ReviewKind


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_summary_matches_the_full_read_for_each_kind(project: Path) -> None:
    save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "main..topic")
    save(project, "f-1", ReviewKind.FEATURE, "2026-01-02T00:00:00+00:00")
    full = list_reviews()
    summaries = list_review_summaries()
    assert [s.id for s in summaries] == [r.id for r in full]
    for summary, review in zip(summaries, full, strict=True):
        assert summary.created_at == review.created_at
        assert summary.repository_root == review.repository_root
        assert summary.file_count == len(review.paths)
        assert summary.origin == review.origin
        assert summary.kind_fields == review.kind_fields()
        counts = review.map_kind.table.line_counts.by_level
        assert (summary.required_lines, summary.total_lines) == (
            counts["required"],
            sum(counts.values()),
        )


def test_summary_reads_neither_the_map_body_nor_background_nor_gates(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("要約の読みで呼ばない")

    for name in ("load_map", "load_background", "load_gates"):
        monkeypatch.setattr(catalog, name, forbidden)
    assert [s.id.key for s in catalog.list_review_summaries()] == ["r-1"]


def test_summary_covers_design_doc_reviews(project: Path) -> None:
    saved = save(project, "d-1", ReviewKind.DESIGN_DOC, "2026-01-03T00:00:00+00:00")
    map_path = saved.id.directory() / "map.json"
    stored = json.loads(map_path.read_text(encoding="utf-8"))
    stored["line_counts"]["by_level"] = {"required": 2, "confirm": 1, "machine": 3}
    map_path.write_text(json.dumps(stored), encoding="utf-8")
    [summary] = list_review_summaries()
    [review] = list_reviews()
    assert summary.origin == review.origin
    assert summary.kind_fields == review.kind_fields()
    assert (summary.required_lines, summary.total_lines) == (2, 6)


def test_summary_skips_a_record_whose_map_is_older_or_newer(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    saved = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")
    map_path = saved.id.directory() / "map.json"
    stored = json.loads(map_path.read_text(encoding="utf-8"))
    del stored["format"]
    map_path.write_text(json.dumps(stored), encoding="utf-8")
    caplog.clear()
    assert list_review_summaries() == []
    assert any("古い形式" in text for text in listing_warnings(caplog))
    stored["format"] = 99
    map_path.write_text(json.dumps(stored), encoding="utf-8")
    caplog.clear()
    assert list_review_summaries() == []
    assert any("新しい" in text for text in listing_warnings(caplog))


def test_summary_skips_a_record_stored_in_the_wrong_place(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    saved = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")
    moved = saved.id.directory().parent / "@elsewhere"
    saved.id.directory().rename(moved)
    caplog.clear()
    assert list_review_summaries() == []
    assert any("指す場所と違います" in text for text in listing_warnings(caplog))


def test_summary_skips_a_record_whose_counts_are_missing_or_not_integers(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    saved = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")
    map_path = saved.id.directory() / "map.json"
    stored = json.loads(map_path.read_text(encoding="utf-8"))
    stored["line_counts"]["by_level"]["required"] = "2"
    map_path.write_text(json.dumps(stored), encoding="utf-8")
    caplog.clear()
    assert list_review_summaries() == []
    assert any("整数ではありません" in text for text in listing_warnings(caplog))
    del stored["line_counts"]
    map_path.write_text(json.dumps(stored), encoding="utf-8")
    caplog.clear()
    assert list_review_summaries() == []
    assert any("読めません" in text for text in listing_warnings(caplog))


def test_summary_skips_a_record_whose_kind_disagrees_with_its_id(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    saved = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")
    review_path = saved.id.directory() / "review.json"
    stored = json.loads(review_path.read_text(encoding="utf-8"))
    stored["local_range"] = None
    stored["targets"] = ["src"]
    review_path.write_text(json.dumps(stored), encoding="utf-8")
    caplog.clear()
    assert list_review_summaries() == []
    assert any("一覧から除きました" in text for text in listing_warnings(caplog))


def _counts() -> dict[str, Any]:
    return {"by_kind": {}, "by_level": {"required": 2, "confirm": 1, "machine": 3}}


@pytest.mark.parametrize("by_level", [{}, {"confirm": 1, "machine": 3}, _counts()["by_level"]])
def test_line_totals_match_the_full_read_when_required_is_missing(
    project: Path, by_level: dict[str, int]
) -> None:
    saved = save(project, "r-1", ReviewKind.FEATURE, "2026-01-01T00:00:00+00:00")
    stored = json.loads((saved.id.directory() / "map.json").read_text(encoding="utf-8"))
    stored["line_counts"]["by_level"] = by_level
    counts = map_kind.load_map(stored).table.line_counts.by_level
    assert map_kind.read_line_totals(stored) == (counts.get("required", 0), sum(counts.values()))


def test_read_review_summary_reports_a_missing_directory_as_review_error(tmp_path: Path) -> None:
    with pytest.raises(ReviewError, match="レビューがありません"):
        catalog._read_review_summary(tmp_path / "absent")  # pyright: ignore[reportPrivateUsage]


def test_read_review_summary_reports_a_broken_map_as_review_error(project: Path) -> None:
    saved = save(project, "r-1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00")
    (saved.id.directory() / "map.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ReviewError, match="保存したレビューを読めません"):
        catalog._read_review_summary(saved.id.directory())  # pyright: ignore[reportPrivateUsage]


def _break_reviews(project: Path, count: int) -> None:
    for number in range(count):
        saved = save(project, f"broken{number}", ReviewKind.FEATURE, "2026-01-01T00:00:00+00:00")
        (saved.id.directory() / "map.json").write_text("{not json", encoding="utf-8")


def test_collapse_skipped_warnings_counts_each_record_once_over_repeated_listings(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _break_reviews(project, 3)
    with catalog.collapse_skipped_warnings():
        assert list_review_summaries() == []
        assert list_review_summaries() == []
    [message] = listing_warnings(caplog)
    assert (
        message == "読めない古い記録が 3 件あります。理由は `jigane list` で 1 件ずつ見られます。"
    )


def test_collapse_skipped_warnings_is_silent_when_nothing_was_skipped(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    save(project, "ok-1", ReviewKind.FEATURE, "2026-01-01T00:00:00+00:00")
    with catalog.collapse_skipped_warnings():
        assert len(list_review_summaries()) == 1
    assert listing_warnings(caplog) == []


def test_collapse_skipped_warnings_still_reports_when_the_block_raises(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _break_reviews(project, 1)

    def list_then_fail() -> None:
        with catalog.collapse_skipped_warnings():
            list_review_summaries()
            raise RuntimeError("途中で失敗")

    with pytest.raises(RuntimeError):
        list_then_fail()
    [message] = listing_warnings(caplog)
    assert "1 件" in message
    # 失敗で抜けても溜める状態は戻り、次の一覧は 1 件ずつ警告する
    caplog.clear()
    list_review_summaries()
    assert len(listing_warnings(caplog)) == 1


def test_show_by_short_id_warns_once_for_all_unreadable_reviews(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _break_reviews(project, 5)
    save(project, "ok-1", ReviewKind.FEATURE, "2026-01-01T00:00:00+00:00")
    # 短い形の解決は一覧を 2 回引くが、入口が囲むので警告は 1 件になる
    main(["show", "shop:ok-1", "overview", "--json"])
    [message] = listing_warnings(caplog)
    assert "5 件" in message


def test_debug_stats_warns_once_for_all_unreadable_reviews(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _break_reviews(project, 5)
    assert main(["debug", "stats", "--json"]) == EXIT_OK
    [message] = listing_warnings(caplog)
    assert "5 件" in message


def test_list_warns_for_each_unreadable_review_with_its_reason(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _break_reviews(project, 5)
    assert main(["list", "--json"]) == EXIT_OK
    assert len(listing_warnings(caplog)) == 5
