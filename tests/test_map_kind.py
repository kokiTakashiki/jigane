"""map.json の読む境（load_map）と、版 1 から今の版への移しを縛る。"""

import json
from pathlib import Path
from typing import Any

import pytest
from store_format_support import check_upgrades

from jigane.judgment_map.map_kind import (
    _UPGRADES,
    MAP_FORMAT,
    CodeMap,
    DesignDocMap,
    MapKind,
    _upgrade_v1,
    _upgrade_v2,
    _upgrade_v3,
    dump_map,
    load_map,
    map_kind_of_mode,
)
from jigane.reviews.lifecycle import ReviewError

STORE = Path(__file__).parent / "fixtures" / "store"

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


def old_map() -> dict[str, Any]:
    return {
        "mode": "diff",
        "segments": [dict(SEGMENT_V1)],
        "regions": [dict(REGION_V1)],
        "line_counts": {"by_kind": {}, "by_level": {}},
    }


def test_v1_upgrade_yields_the_keys_the_current_writer_writes() -> None:
    upgraded = _upgrade_v3(_upgrade_v2(_upgrade_v1(old_map())))

    expected = {key: value for key, value in current_map().items() if key != "format"}
    assert set(upgraded) == set(expected)
    assert set(upgraded["segments"][0]) == set(expected["segments"][0])
    assert set(upgraded["regions"][0]) == set(expected["regions"][0])
    assert upgraded == expected


def test_v1_upgrade_does_not_change_its_input() -> None:
    raw = old_map()

    _upgrade_v1(raw)

    assert raw == old_map()


def test_current_map_reads_and_writes_back_to_the_same_dict() -> None:
    stored = current_map()

    kind = load_map(stored)

    assert isinstance(kind, CodeMap)
    assert dump_map(kind) == stored
    assert "format" not in kind.to_dict()


def test_version_1_and_current_files_read_to_the_same_value() -> None:
    assert load_map(old_map()) == load_map(current_map())


def test_design_doc_map_current_form_is_upgraded_too() -> None:
    raw = {**old_map(), "mode": "design_doc", "current_form": old_map()}

    kind = load_map(raw)

    assert isinstance(kind, DesignDocMap)
    written = dump_map(kind)
    assert written["format"] == MAP_FORMAT
    assert "format" not in written["current_form"]
    assert written["current_form"]["line_counts"]["by_kind"] == {
        "internal_interface": 0,
        "static_finding": 0,
    }


def test_design_doc_section_table_keeps_the_section_vocabulary_on_upgrade() -> None:
    sections = {**old_map(), "line_counts": {"by_kind": {"overview": 3}, "by_level": {}}}
    raw = {**sections, "mode": "design_doc", "current_form": old_map()}

    written = dump_map(load_map(raw))

    assert written["line_counts"]["by_kind"] == {"overview": 3}
    assert written["current_form"]["line_counts"]["by_kind"] == {
        "internal_interface": 0,
        "static_finding": 0,
    }


def test_future_format_fails_with_a_review_error() -> None:
    raw = {**current_map(), "format": MAP_FORMAT + 1}

    with pytest.raises(ReviewError, match="より新しい"):
        load_map(raw)


@pytest.mark.parametrize("version", ["2", None, 1.5, True, 0])
def test_unusable_format_fails_with_a_review_error(version: object) -> None:
    raw = {**current_map(), "format": version}

    with pytest.raises(ReviewError, match="形の版"):
        load_map(raw)


STORED_MAPS = sorted(STORE.glob("map/*/*/*/*/map.json"))


def test_there_are_stored_maps_to_read() -> None:
    assert STORED_MAPS


@pytest.mark.parametrize("path", STORED_MAPS, ids=lambda p: p.parent.parent.name)
def test_every_stored_map_reads_and_is_written_in_the_current_format(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))

    kind = load_map(raw)

    written = dump_map(kind)
    assert written["format"] == MAP_FORMAT
    assert load_map(written) == kind


def test_upgrade_table_has_every_step() -> None:
    check_upgrades("map.json", MAP_FORMAT, _UPGRADES)


SECTION_ROW_KEYS = {"bot_comment_ids", "old_path"}


def design_doc_map_v2() -> dict[str, Any]:
    """版 2 の設計書の地図。節の行が空の二つの鍵を持つ。"""
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
        "bot_comment_ids": [],
        "old_path": None,
        "id": "J1",
    }
    current_form = {key: value for key, value in current_map().items() if key != "format"}
    return {
        "format": 2,
        "mode": "design_doc",
        "segments": current_form["segments"],
        "regions": [section],
        "line_counts": {"by_kind": {"non_goal": 4}, "by_level": {}},
        "current_form": {
            "regions": current_form["regions"],
            "line_counts": current_form["line_counts"],
        },
    }


def test_v2_upgrade_drops_the_section_row_keys_and_keeps_the_code_row_keys() -> None:
    raw = design_doc_map_v2()

    upgraded = _upgrade_v2(raw)

    assert SECTION_ROW_KEYS.isdisjoint(upgraded["regions"][0])
    assert set(upgraded["current_form"]["regions"][0]) >= SECTION_ROW_KEYS
    assert raw == design_doc_map_v2()


def test_version_2_design_doc_map_reads_and_is_written_without_the_section_row_keys() -> None:
    kind = load_map(design_doc_map_v2())

    assert isinstance(kind, DesignDocMap)
    written = dump_map(kind)
    assert written["format"] == MAP_FORMAT
    assert SECTION_ROW_KEYS.isdisjoint(written["regions"][0])
    assert set(written["current_form"]["regions"][0]) >= SECTION_ROW_KEYS
    assert load_map(written) == kind


def test_section_row_keys_are_printed_but_not_stored() -> None:
    kind = load_map(design_doc_map_v2())

    assert isinstance(kind, DesignDocMap)
    printed = kind.to_dict()["regions"][0]
    assert printed["bot_comment_ids"] == []
    assert printed["old_path"] is None
    assert SECTION_ROW_KEYS.isdisjoint(kind.to_stored_dict()["regions"][0])


def test_v2_upgrade_keeps_the_code_row_keys_of_a_code_map() -> None:
    raw = {**current_map(), "format": 2}

    upgraded = _upgrade_v2(raw)

    assert upgraded == raw
    assert set(upgraded["regions"][0]) >= SECTION_ROW_KEYS


def test_version_2_code_map_reads_and_is_written_with_the_code_row_keys() -> None:
    kind = load_map({**current_map(), "format": 2})

    assert isinstance(kind, CodeMap)
    written = dump_map(kind)
    assert written["format"] == MAP_FORMAT
    assert set(written["regions"][0]) >= SECTION_ROW_KEYS
    assert load_map(written) == kind


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


def test_v3_upgrade_fills_empty_finding_ids_and_the_new_kind_count() -> None:
    raw = {**current_map(), "format": 3}
    for segment in raw["segments"]:
        del segment["static_finding_ids"]
    del raw["line_counts"]["by_kind"]["static_finding"]
    snapshot = json.dumps(raw, sort_keys=True)

    upgraded = _upgrade_v3(raw)

    assert upgraded["segments"][0]["static_finding_ids"] == []
    assert upgraded["line_counts"]["by_kind"]["static_finding"] == 0
    assert json.dumps(raw, sort_keys=True) == snapshot


def test_version_3_code_map_reads_and_is_written_in_version_4() -> None:
    raw = {**current_map(), "format": 3}
    del raw["segments"][0]["static_finding_ids"]

    kind = load_map(raw)

    assert isinstance(kind, CodeMap)
    assert kind.segments[0].static_finding_ids == ()
    written = dump_map(kind)
    assert written["format"] == 4
    assert written["segments"][0]["static_finding_ids"] == []
    assert load_map(written) == kind


def test_version_3_design_doc_map_puts_the_new_kind_count_under_current_form() -> None:
    raw = design_doc_map_v2()
    raw["format"] = 3
    for segment in raw["segments"]:
        del segment["static_finding_ids"]
    del raw["current_form"]["line_counts"]["by_kind"]["static_finding"]
    raw["regions"][0].pop("bot_comment_ids")
    raw["regions"][0].pop("old_path")

    kind = load_map(raw)

    assert isinstance(kind, DesignDocMap)
    written = dump_map(kind)
    assert "static_finding" not in written["line_counts"]["by_kind"]
    assert written["current_form"]["line_counts"]["by_kind"]["static_finding"] == 0


def test_version_4_map_with_finding_ids_reads_back_unchanged() -> None:
    raw = current_map()
    raw["segments"][0] |= {"kind": "static_finding", "level": "required"}
    raw["segments"][0]["static_finding_ids"] = ["S1", "S2"]

    kind = load_map(raw)

    assert isinstance(kind, CodeMap)
    assert kind.segments[0].static_finding_ids == ("S1", "S2")
    assert dump_map(kind) == raw
