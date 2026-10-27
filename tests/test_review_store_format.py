"""review.json、regions.json、pairs.json の形の版: 読む境で古い版を移し、書く境で今の版を刻む。"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jigane.judgment_map.classification import FilePair
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import (
    PAIRS_FORMAT,
    REGIONS_FORMAT,
    REVIEW_FORMAT,
    create_feature_review,
    dump_pairs,
    dump_regions,
    dump_review,
    load_regions,
    load_review_at,
    load_review_meta,
    read_pairs,
    read_review_at,
)
from jigane.storage.review_error import ReviewError

STORE = Path(__file__).parent / "fixtures" / "store"

DECLARATION_V1: dict[str, Any] = {"source": "intent", "intent": "text", "excerpts": []}
PULL_REQUEST_FIELDS: dict[str, Any] = {
    "url": "https://example.invalid/pull/1",
    "number": 1,
    "title": "title",
    "linked_issues": [],
}
DIFF_FIELDS: dict[str, str] = {
    "base_sha": "b",
    "head_sha": "h",
    "merge_base": "m",
    "base_tree": "/t",
}

# 版 4 で保存から外した鍵。id や宣言から導けるか、読み口が無い
DERIVED_REVIEW_KEYS = {"kind", "mode", "is_diff", "is_pull_request", "warnings"}

# 版 1 の各形の最小の辞書。鍵の欠け方が違う順に、機能、古い PR、新しい形の PR
MINIMAL_REVIEW_V1: dict[str, dict[str, Any]] = {
    "feature": {
        "id": "owner/repo:sample",
        "mode": "feature",
        "created_at": "2026-10-17T09:35:07+00:00",
        "repository_root": "/r",
        "paths": ["a.py"],
        "declaration": DECLARATION_V1,
        "warnings": ["w"],
    },
    "pull_request_with_merged_source": {
        "id": "owner/repo#1",
        "mode": "diff",
        "created_at": "2026-10-17T09:35:07+00:00",
        "refreshed_at": None,
        "repository_root": "/r",
        "paths": ["a.py"],
        "targets": None,
        "declaration": DECLARATION_V1,
        "warnings": [],
        "pull_request": {**PULL_REQUEST_FIELDS, **DIFF_FIELDS},
        "background": None,
    },
    "pull_request_with_separated_source": {
        "id": "owner/repo#1",
        "kind": "pull_request",
        "mode": "diff",
        "is_diff": True,
        "is_pull_request": True,
        "created_at": "2026-10-17T09:35:07+00:00",
        "refreshed_at": None,
        "repository_root": "/r",
        "paths": ["a.py"],
        "targets": None,
        "declaration": {**DECLARATION_V1, "source_label": "label", "warnings": []},
        "warnings": [],
        "content_warnings": [],
        "diff_source": DIFF_FIELDS,
        "pull_request": PULL_REQUEST_FIELDS,
        "local_range": None,
        "design_doc": None,
        "background": None,
    },
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# Shop\n\nlead\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _written_review_keys(project: Path) -> tuple[set[str], set[str]]:
    created = create_feature_review(project, ["src"], "payment")
    written = json.loads((created.id.directory() / "review.json").read_text(encoding="utf-8"))
    return set(written) - {"format"}, set(written["declaration"])


@pytest.mark.parametrize("shape", MINIMAL_REVIEW_V1)
def test_review_v1_shape_upgrades_to_the_keys_the_current_writer_writes(
    shape: str, project: Path
) -> None:
    upgraded = load_review_meta(MINIMAL_REVIEW_V1[shape])
    keys, declaration_keys = _written_review_keys(project)
    assert set(upgraded) == keys
    assert set(upgraded["declaration"]) == declaration_keys


def test_review_upgrade_fills_defaults_and_does_not_touch_the_input() -> None:
    raw = MINIMAL_REVIEW_V1["feature"]
    upgraded = review_module._REVIEW_UPGRADES[1](raw)
    assert "content_warnings" not in raw
    assert upgraded["content_warnings"] == ["w"]
    assert upgraded["declaration"]["warnings"] == []
    assert upgraded["targets"] is None
    assert upgraded["refreshed_at"] is None


def test_review_upgrade_splits_the_merged_pull_request_value() -> None:
    upgraded = review_module._REVIEW_UPGRADES[1](
        MINIMAL_REVIEW_V1["pull_request_with_merged_source"]
    )
    assert upgraded["pull_request"] == PULL_REQUEST_FIELDS
    assert upgraded["diff_source"] == DIFF_FIELDS


def test_review_v2_drops_linked_issues_from_pull_request() -> None:
    raw = {
        **MINIMAL_REVIEW_V1["pull_request_with_separated_source"],
        "format": 2,
    }
    upgraded = review_module._REVIEW_UPGRADES[2](raw)
    assert upgraded["pull_request"] == {
        "url": PULL_REQUEST_FIELDS["url"],
        "number": 1,
        "title": "title",
    }
    # 入力は書き換えない
    assert "linked_issues" in raw["pull_request"]


def test_review_v2_keeps_a_record_without_pull_request() -> None:
    upgraded = review_module._REVIEW_UPGRADES[2]({"pull_request": None, "id": "x"})
    assert upgraded == {"pull_request": None, "id": "x"}


def test_review_v1_merged_source_reads_through_to_the_current_version() -> None:
    raw = MINIMAL_REVIEW_V1["pull_request_with_merged_source"]
    loaded = review_module.load_review_meta(raw)
    assert set(loaded["pull_request"]) == {"url", "number", "title"}
    assert loaded["diff_source"] == DIFF_FIELDS


def test_review_upgrade_keeps_a_separated_pull_request_as_it_is() -> None:
    raw = MINIMAL_REVIEW_V1["pull_request_with_separated_source"]
    assert review_module._REVIEW_UPGRADES[1](raw) == raw


def test_review_round_trips_through_the_write_boundary(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    located = read_review_at(created.id.directory())
    review_module._write_review_file(located.directory, located.review)
    stored = json.loads((located.directory / "review.json").read_text(encoding="utf-8"))
    assert stored["format"] == REVIEW_FORMAT
    assert load_review_at(located.directory) == located.review


def test_dump_review_puts_the_format_on_the_top_level_only() -> None:
    assert dump_review({"id": "x"}) == {"format": REVIEW_FORMAT, "id": "x"}


def test_regions_v1_upgrade_drops_the_unread_judged_key() -> None:
    raw: dict[str, Any] = {
        "next_number": 2,
        "regions": {"k": {"id": "J1", "content_hash": "h", "judged": False, "gone": True}},
    }
    upgraded = load_regions(raw)
    assert upgraded["regions"]["k"] == {"id": "J1", "content_hash": "h", "gone": True}
    assert "judged" in raw["regions"]["k"]


def test_regions_round_trip() -> None:
    state = {"next_number": 2, "regions": {"k": {"id": "J1", "content_hash": "h"}}}
    dumped = dump_regions(state)
    assert dumped["format"] == REGIONS_FORMAT
    assert load_regions(json.loads(json.dumps(dumped))) == state


def test_regions_v1_keys_match_the_current_writer() -> None:
    raw = {"next_number": 1, "regions": {}}
    assert set(load_regions(raw)) | {"format"} == set(dump_regions(raw))


def test_pairs_v1_array_upgrades_to_the_current_shape() -> None:
    raw = [["a.py", "a.py"], [None, "b.py"], ["c.py", None]]
    assert read_pairs(raw) == [
        FilePair("a.py", "a.py"),
        FilePair(None, "b.py"),
        FilePair("c.py", None),
    ]
    assert review_module._PAIRS_UPGRADES[1](raw) == {"pairs": raw}


def test_pairs_round_trip() -> None:
    pairs = [FilePair("a.py", "a.py"), FilePair(None, "b.py")]
    dumped = dump_pairs(pairs)
    assert dumped["format"] == PAIRS_FORMAT
    assert read_pairs(json.loads(json.dumps(dumped))) == pairs


@pytest.mark.parametrize("version", [99, 0, -1, True, "2", 1.5, None])
@pytest.mark.parametrize(
    "load",
    [
        lambda version: load_review_meta({**MINIMAL_REVIEW_V1["feature"], "format": version}),
        lambda version: load_regions({"format": version, "next_number": 1, "regions": {}}),
        lambda version: read_pairs({"format": version, "pairs": []}),
    ],
    ids=["review", "regions", "pairs"],
)
def test_unusable_format_fails_with_a_review_error(
    version: object, load: Callable[[object], object]
) -> None:
    with pytest.raises(ReviewError):
        load(version)


def test_a_broken_top_level_fails_with_a_review_error() -> None:
    with pytest.raises(ReviewError):
        load_review_meta([])
    with pytest.raises(ReviewError):
        load_regions([])
    with pytest.raises(ReviewError):
        read_pairs("x")


@pytest.mark.parametrize("name", ["review", "regions", "pairs"])
def test_fixtures_exist(name: str) -> None:
    assert list(STORE.glob(f"{name}/*/*/*/*/{name}.json"))


@pytest.mark.parametrize("path", sorted(STORE.glob("regions/*/*/*/*/regions.json")), ids=str)
def test_every_stored_regions_reads_and_is_written_in_the_current_format(path: Path) -> None:
    state = load_regions(json.loads(path.read_text(encoding="utf-8")))
    assert dump_regions(state)["format"] == REGIONS_FORMAT


@pytest.mark.parametrize("path", sorted(STORE.glob("pairs/*/*/*/*/pairs.json")), ids=str)
def test_every_stored_pairs_reads_and_is_written_in_the_current_format(path: Path) -> None:
    pairs = read_pairs(json.loads(path.read_text(encoding="utf-8")))
    assert pairs
    assert dump_pairs(pairs)["format"] == PAIRS_FORMAT


@pytest.mark.parametrize("path", sorted(STORE.glob("review/*/*/*/*/review.json")), ids=str)
def test_every_stored_review_meta_upgrades_to_the_current_keys(path: Path) -> None:
    upgraded = load_review_meta(json.loads(path.read_text(encoding="utf-8")))
    assert {"content_warnings", "diff_source", "design_doc", "refreshed_at"} <= set(upgraded)
    assert not set(upgraded) & DERIVED_REVIEW_KEYS
    assert "source_label" not in upgraded["declaration"]


def test_review_v3_drops_the_derivable_and_write_only_keys() -> None:
    raw: dict[str, Any] = {
        "id": "owner/repo:sample",
        "kind": "feature",
        "mode": "feature",
        "is_diff": False,
        "is_pull_request": False,
        "warnings": ["w"],
        "content_warnings": ["w"],
        "declaration": {**DECLARATION_V1, "source_label": "x", "warnings": []},
    }
    upgraded = review_module._REVIEW_UPGRADES[3](raw)
    assert upgraded == {
        "id": raw["id"],
        "content_warnings": ["w"],
        "declaration": {**DECLARATION_V1, "warnings": []},
    }
    # 入力は書き換えない
    assert "kind" in raw
    assert "source_label" in raw["declaration"]


def test_review_is_stored_without_the_derivable_keys_and_printed_with_them(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    stored = json.loads((created.id.directory() / "review.json").read_text(encoding="utf-8"))
    assert not set(stored) & DERIVED_REVIEW_KEYS
    assert "source_label" not in stored["declaration"]
    printed = created.to_dict()
    assert set(printed) >= DERIVED_REVIEW_KEYS
    assert printed["declaration"]["source_label"]
    # 保存の鍵は一つ残らず出力にも出る
    assert set(printed) >= set(created.to_stored_dict())
