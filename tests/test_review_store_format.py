"""review.json、regions.json、pairs.json の形の版: 読む境で古い版を止め、書く境で今の版を刻む。"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jigane.judgment_map.classification import FilePair
from jigane.reviews import lifecycle as review_module
from jigane.reviews.catalog import load_review_at, read_review_at
from jigane.reviews.lifecycle import create_feature_review
from jigane.reviews.review_formats import (
    PAIRS_FORMAT,
    REGIONS_FORMAT,
    REVIEW_FORMAT,
    dump_pairs,
    dump_regions,
    dump_review,
    load_regions,
    load_review_meta,
    read_pairs,
)
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import OlderFormatError

STORE = Path(__file__).parent / "fixtures" / "store"

DECLARATION: dict[str, Any] = {"source": "intent", "intent": "text", "excerpts": []}
# 版 4 で保存から外した鍵。id や宣言から導けるか、読み口が無い
DERIVED_REVIEW_KEYS = {"kind", "mode", "is_diff", "is_pull_request", "warnings"}

# ファイルのレビューの最小の辞書。版は format で足す
MINIMAL_REVIEW: dict[str, Any] = {
    "id": "owner/repo:sample",
    "mode": "feature",
    "created_at": "2026-10-17T09:35:07+00:00",
    "repository_root": "/r",
    "paths": ["a.py"],
    "declaration": DECLARATION,
    "warnings": ["w"],
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# Shop\n\nlead\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_review_round_trips_through_the_write_boundary(project: Path) -> None:
    created = create_feature_review(project, ["src"], "payment")
    located = read_review_at(created.id.directory())
    review_module.write_review_file(located.directory, located.review)
    stored = json.loads((located.directory / "review.json").read_text(encoding="utf-8"))
    assert stored["format"] == REVIEW_FORMAT
    assert load_review_at(located.directory) == located.review


def test_dump_review_puts_the_format_on_the_top_level_only() -> None:
    assert dump_review({"id": "x"}) == {"format": REVIEW_FORMAT, "id": "x"}


def test_regions_round_trip() -> None:
    state = {"next_number": 2, "regions": {"k": {"id": "J1", "content_hash": "h"}}}
    dumped = dump_regions(state)
    assert dumped["format"] == REGIONS_FORMAT
    assert load_regions(json.loads(json.dumps(dumped))) == state


def test_regions_keys_match_the_current_writer() -> None:
    raw = {"format": REGIONS_FORMAT, "next_number": 1, "regions": {}}
    assert set(load_regions(raw)) | {"format"} == set(dump_regions(raw))


def test_pairs_round_trip() -> None:
    pairs = [FilePair("a.py", "a.py"), FilePair(None, "b.py")]
    dumped = dump_pairs(pairs)
    assert dumped["format"] == PAIRS_FORMAT
    assert read_pairs(json.loads(json.dumps(dumped))) == pairs


@pytest.mark.parametrize("version", [99, 0, -1, True, "2", 1.5, None])
@pytest.mark.parametrize(
    "load",
    [
        lambda version: load_review_meta({**MINIMAL_REVIEW, "format": version}),
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


STORED_LOADERS: dict[str, Callable[[object], object]] = {
    "review": load_review_meta,
    "regions": load_regions,
    "pairs": read_pairs,
}


STORED_FIXTURES = [
    (name, path)
    for name in STORED_LOADERS
    for path in sorted(STORE.glob(f"{name}/*/*/*/*/{name}.json"))
]


@pytest.mark.parametrize(("name", "path"), STORED_FIXTURES, ids=str)
def test_every_stored_fixture_is_an_older_version_and_stops(name: str, path: Path) -> None:
    with pytest.raises(OlderFormatError, match="jigane new"):
        STORED_LOADERS[name](json.loads(path.read_text(encoding="utf-8")))


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


def test_newer_review_json_stops_with_the_shared_wording() -> None:
    future = REVIEW_FORMAT + 1
    with pytest.raises(ReviewError) as caught:
        load_review_meta({**MINIMAL_REVIEW, "format": future})
    message = str(caught.value)
    assert message.startswith("review.json は、この jigane より新しいバージョンの jigane が作った")
    assert f"JSON の構造のバージョンは {future} で、" in message
    assert f"この jigane が読み込めるのは {REVIEW_FORMAT} までです" in message
    assert "`uv tool upgrade jigane`" in message
    assert "`pipx upgrade jigane`" in message


OLDER_CASES: list[tuple[str, object]] = [
    *[("review", {**MINIMAL_REVIEW, "format": version}) for version in (1, 2, 3, 4)],
    ("review", MINIMAL_REVIEW),
    ("review", {}),
    ("regions", {"format": 1, "next_number": 1, "regions": {}}),
    ("regions", {"next_number": 1, "regions": {}}),
    ("pairs", [["a.py", "a.py"]]),
    ("pairs", []),
]


@pytest.mark.parametrize(("name", "raw"), OLDER_CASES, ids=str)
def test_older_versions_stop_with_the_rebuild_hint(name: str, raw: object) -> None:
    with pytest.raises(OlderFormatError) as caught:
        STORED_LOADERS[name](raw)
    message = str(caught.value)
    assert f"{name}.json" in message
    assert "`jigane new`" in message
    assert "引き継がれません" in message


def test_newer_and_broken_versions_keep_their_own_wording() -> None:
    with pytest.raises(ReviewError, match="より新しいバージョン") as newer:
        load_regions({"format": REGIONS_FORMAT + 1, "next_number": 1, "regions": {}})
    assert not isinstance(newer.value, OlderFormatError)
    with pytest.raises(ReviewError, match="整数ではありません") as broken:
        read_pairs({"format": "2", "pairs": []})
    assert not isinstance(broken.value, OlderFormatError)
