"""selection.json、background.json の形の版を縛る。"""

import json
from pathlib import Path

import pytest

from jigane.pull_request_context.background import (
    BACKGROUND_FORMAT,
    Background,
    PastPullRequest,
    load_background,
    parse_background,
    save_background,
)
from jigane.reviews.selection import (
    SELECTION_FORMAT,
    Held,
    Objected,
    Picked,
    Selection,
    load_selection,
    parse_selection,
    save_selection,
)
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import OlderFormatError

STORE = Path(__file__).parent / "fixtures" / "store"
BAD_VERSIONS = [0, -1, 99, "2", 1.5, True, None]


def stored_files(kind: str, file: str) -> list[Path]:
    return sorted((STORE / kind).glob(f"*/*/*/*/{file}"))


def fixture_id(path: Path) -> str:
    return "-".join(path.relative_to(STORE).parts[:2])


@pytest.mark.parametrize("path", stored_files("selection", "selection.json"), ids=fixture_id)
def test_selection_fixtures_are_older_versions_and_stop(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    with pytest.raises(OlderFormatError, match="jigane new"):
        parse_selection(raw)
    with pytest.raises(OlderFormatError, match="jigane new"):
        load_selection(path.parent)


@pytest.mark.parametrize("path", stored_files("background", "background.json"), ids=fixture_id)
def test_background_fixtures_are_older_formats_and_stop(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    with pytest.raises(OlderFormatError, match="jigane new"):
        parse_background(raw)
    with pytest.raises(OlderFormatError, match="引き継がれません"):
        load_background(path.parent)


@pytest.mark.parametrize(
    "older",
    [
        {"picked": ["F1"], "held": [], "objections": {}},
        {"format": 2, "picked": ["F1"], "held": ["F2"], "objections": {"F3": "理由"}},
        {"format": 3, "verdicts": {"F1": {"kind": "picked"}}},
    ],
    ids=["v1", "v2", "v3"],
)
def test_older_selection_stops_with_the_rebuild_hint(older: dict[str, object]) -> None:
    with pytest.raises(OlderFormatError) as caught:
        parse_selection(older)
    assert "`jigane new`" in str(caught.value)
    assert "引き継がれません" in str(caught.value)


def test_selection_json_keys_are_derived_from_verdicts() -> None:
    selection = Selection({"F10": Picked(), "F2": Picked(), "F3": Held(), "F4": Objected("理由")})
    assert selection.to_dict() == {
        "picked": ["F2", "F10"],
        "held": ["F3"],
        "objections": {"F4": "理由"},
        "hold_reasons": {},
    }


def test_selection_round_trip(tmp_path: Path) -> None:
    selection = Selection({"F1": Picked(), "F2": Held(), "F3": Objected("理由")})
    save_selection(tmp_path, selection)
    stored = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert stored["format"] == SELECTION_FORMAT
    assert load_selection(tmp_path) == selection


@pytest.mark.parametrize("version", BAD_VERSIONS)
def test_selection_rejects_bad_version(version: object) -> None:
    with pytest.raises(ReviewError):
        parse_selection({"format": version, "verdicts": {}})


@pytest.mark.parametrize("raw", [{"documents": []}, {"format": 1, "documents": []}, {"format": 2}])
def test_background_older_versions_stop_with_the_rebuild_hint(raw: dict[str, object]) -> None:
    with pytest.raises(OlderFormatError) as caught:
        parse_background(raw)
    assert "jigane new" in str(caught.value)
    assert "引き継がれません" in str(caught.value)


def test_background_newer_version_is_a_plain_review_error() -> None:
    with pytest.raises(ReviewError, match="新しいバージョン") as caught:
        parse_background({"format": BACKGROUND_FORMAT + 1, "documents": []})
    assert not isinstance(caught.value, OlderFormatError)


def test_background_round_trip(tmp_path: Path) -> None:
    background = Background(("d.md",), (PastPullRequest(1, "t", ("a",)),), ())
    save_background(tmp_path, background)
    stored = json.loads((tmp_path / "background.json").read_text(encoding="utf-8"))
    assert stored["format"] == BACKGROUND_FORMAT
    assert parse_background(stored) == background


@pytest.mark.parametrize("version", BAD_VERSIONS)
def test_background_rejects_bad_version(version: object) -> None:
    with pytest.raises(ReviewError):
        parse_background({"format": version, "issues": [], "documents": []})
