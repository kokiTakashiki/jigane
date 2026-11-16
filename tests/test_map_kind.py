"""map.json の読む境（load_map、read_line_totals）が今の版だけを読み、古い版で止まること。"""

from typing import Any

import pytest

from jigane.judgment_map.map_kind import (
    MAP_FORMAT,
    CodeMap,
    DesignDocMap,
    MapKind,
    dump_map,
    load_map,
    map_kind_of_mode,
    read_line_totals,
)
from jigane.reviews.lifecycle import ReviewError
from jigane.storage.store_format import OlderFormatError

SEGMENT_V1: dict[str, Any] = {
    "old_path": "a.py",
    "new_path": "a.py",
    "old_span": None,
    "new_span": {"start_line": 1, "end_line": 2},
    "qualified_name": "f",
    "kind": "logic",
    "level": "confirm",
    "old_line_count": 0,
    "new_line_count": 2,
}
REGION_V1: dict[str, Any] = {
    "key": "a.py::f::logic",
    "kind": "logic",
    "path": "a.py",
    "qualified_name": "f",
    "summary": "",
    "old_span": None,
    "new_span": {"start_line": 1, "end_line": 2},
    "line_count": 2,
    "content_hash": "h",
    "id": "J1",
    "judged": False,
}


def current_map() -> dict[str, Any]:
    segment = {
        **SEGMENT_V1,
        "declaration_kind": None,
        "change_kind": "added",
        "bot_comment_ids": [],
        "visibility": None,
        "static_finding_ids": [],
    }
    region = {key: value for key, value in REGION_V1.items() if key != "judged"} | {
        "bot_comment_ids": [],
        "old_path": None,
    }
    return {
        "format": MAP_FORMAT,
        "mode": "diff",
        "segments": [segment],
        "regions": [region],
        "line_counts": {
            "by_kind": {"internal_interface": 0, "static_finding": 0},
            "by_level": {},
        },
    }


def test_current_map_reads_and_writes_back_to_the_same_dict() -> None:
    stored = current_map()

    kind = load_map(stored)

    assert isinstance(kind, CodeMap)
    assert dump_map(kind) == stored
    assert "format" not in kind.to_dict()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_an_older_map_stops_with_the_rebuild_hint(version: int) -> None:
    raw = {**current_map(), "format": version}

    with pytest.raises(OlderFormatError) as caught:
        load_map(raw)

    message = str(caught.value)
    assert "map.json" in message
    assert f"バージョンは {version}" in message
    assert "jigane new" in message
    assert "引き継がれません" in message


def test_a_map_without_a_format_is_read_as_version_1_and_stops() -> None:
    raw = {key: value for key, value in current_map().items() if key != "format"}

    with pytest.raises(OlderFormatError, match="バージョンは 1 "):
        load_map(raw)


def test_read_line_totals_reads_the_current_version_and_stops_on_an_older_one() -> None:
    raw = current_map()
    raw["line_counts"]["by_level"] = {"required": 2, "confirm": 3}
    assert read_line_totals(raw) == (2, 5)

    with pytest.raises(OlderFormatError, match="jigane new"):
        read_line_totals({**raw, "format": 3})
    with pytest.raises(ReviewError, match="新しいバージョン"):
        read_line_totals({**raw, "format": MAP_FORMAT + 1})


def test_future_format_fails_with_a_review_error() -> None:
    raw = {**current_map(), "format": MAP_FORMAT + 1}

    with pytest.raises(ReviewError, match="JSON の構造のバージョン"):
        load_map(raw)


@pytest.mark.parametrize("version", ["2", None, 1.5, True, 0])
def test_unusable_format_fails_with_a_review_error(version: object) -> None:
    raw = {**current_map(), "format": version}

    with pytest.raises(ReviewError, match="形の版"):
        load_map(raw)


SECTION_ROW_KEYS = {"bot_comment_ids", "old_path"}


def design_doc_map() -> dict[str, Any]:
    """今の版の設計書の地図。節の行は空の二つの鍵を保存しない。"""
    section: dict[str, Any] = {
        "key": "d.md::2. 非目標::non_goal",
        "kind": "non_goal",
        "kind_label": "非目標",
        "path": "d.md",
        "qualified_name": "2. 非目標",
        "summary": "2. 非目標",
        "old_span": None,
        "new_span": {"start_line": 3, "end_line": 6},
        "line_count": 4,
        "content_hash": "def",
        "id": "J1",
    }
    current_form = {key: value for key, value in current_map().items() if key != "format"}
    return {
        "format": MAP_FORMAT,
        "mode": "design_doc",
        "segments": current_form["segments"],
        "regions": [section],
        "line_counts": {"by_kind": {"non_goal": 4}, "by_level": {}},
        "current_form": {
            "regions": current_form["regions"],
            "line_counts": current_form["line_counts"],
        },
    }


def test_section_row_keys_are_printed_but_not_stored() -> None:
    kind = load_map(design_doc_map())

    assert isinstance(kind, DesignDocMap)
    printed = kind.to_dict()["regions"][0]
    assert printed["bot_comment_ids"] == []
    assert printed["old_path"] is None
    assert SECTION_ROW_KEYS.isdisjoint(kind.to_stored_dict()["regions"][0])


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("diff", MapKind.CODE), ("feature", MapKind.CODE), ("design_doc", MapKind.DESIGN_DOC)],
)
def test_map_kind_of_known_mode(mode: str, expected: MapKind) -> None:
    assert map_kind_of_mode(mode) is expected


@pytest.mark.parametrize("mode", ["", "pr", "Diff", "design-doc"])
def test_map_kind_of_unknown_mode_is_unreadable_store(mode: str) -> None:
    with pytest.raises(ReviewError, match="mode"):
        map_kind_of_mode(mode)


def test_load_map_rejects_unknown_mode() -> None:
    with pytest.raises(ReviewError, match="mode"):
        load_map({**current_map(), "mode": "bogus"})


def test_version_4_map_with_finding_ids_reads_back_unchanged() -> None:
    raw = current_map()
    raw["segments"][0] |= {"kind": "static_finding", "level": "required"}
    raw["segments"][0]["static_finding_ids"] = ["S1", "S2"]

    kind = load_map(raw)

    assert isinstance(kind, CodeMap)
    assert kind.segments[0].static_finding_ids == ("S1", "S2")
    assert dump_map(kind) == raw
