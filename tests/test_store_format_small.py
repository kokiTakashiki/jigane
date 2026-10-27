"""selection.json、background.json の形の版を縛る。"""

import json
from pathlib import Path

import pytest

from jigane.pull_request_context.background import (
    BACKGROUND_FORMAT,
    Background,
    PastCommit,
    PastPullRequest,
    dump_background,
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
    dump_selection,
    load_selection,
    parse_selection,
    save_selection,
)
from jigane.storage.review_error import ReviewError

STORE = Path(__file__).parent / "fixtures" / "store"
BAD_VERSIONS = [0, -1, 99, "2", 1.5, True, None]


def stored_files(kind: str, file: str) -> list[Path]:
    return sorted((STORE / kind).glob(f"*/*/*/*/{file}"))


def fixture_id(path: Path) -> str:
    return "-".join(path.relative_to(STORE).parts[:2])


@pytest.mark.parametrize("path", stored_files("selection", "selection.json"), ids=fixture_id)
def test_selection_fixtures_read_to_the_stored_values(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    selection = parse_selection(raw)
    assert (selection.picked, selection.held, selection.objections) == (
        raw["picked"],
        raw["held"],
        raw["objections"],
    )
    assert dump_selection(selection)["format"] == SELECTION_FORMAT
    assert load_selection(path.parent) == selection


def test_selection_fixtures_cover_each_field() -> None:
    selections = [
        parse_selection(json.loads(path.read_text(encoding="utf-8")))
        for path in stored_files("selection", "selection.json")
    ]
    assert any(item.picked for item in selections)
    assert any(item.held for item in selections)
    assert any(item.objections for item in selections)


@pytest.mark.parametrize("path", stored_files("background", "background.json"), ids=fixture_id)
def test_background_fixtures_read_to_the_stored_values(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    background = parse_background(raw)
    # 版 1 の旧い形は past_pull_requests、次の形は past_changes に過去の変更を持つ
    stored_changes = raw.get("past_changes", raw.get("past_pull_requests"))
    assert background.documents == tuple(raw["documents"])
    assert [item.path for item in background.owners] == [item["path"] for item in raw["owners"]]
    assert [change.files for change in background.past_changes] == [
        tuple(change["files"]) for change in stored_changes
    ]
    for change, stored in zip(background.past_changes, stored_changes, strict=True):
        if stored.get("kind") == "commit":
            assert change == PastCommit(stored["sha"], stored["title"], tuple(stored["files"]))
        else:
            assert change == PastPullRequest(
                stored["number"], stored["title"], tuple(stored["files"])
            )
    assert load_background(path.parent) == background


def test_background_fixtures_cover_both_past_change_kinds() -> None:
    kinds = {
        type(change)
        for path in stored_files("background", "background.json")
        for change in parse_background(json.loads(path.read_text(encoding="utf-8"))).past_changes
    }
    assert kinds == {PastCommit, PastPullRequest}


def test_selection_v1_moves_to_current_keys() -> None:
    v1 = {"picked": ["F1"], "held": [], "objections": {}}
    moved = dump_selection(parse_selection(v1))
    assert set(moved) == set(dump_selection(Selection()))
    assert moved == {"format": SELECTION_FORMAT, "verdicts": {"F1": {"kind": "picked"}}}


def test_selection_v2_moves_each_collection_to_one_verdict() -> None:
    v2 = {"format": 2, "picked": ["F1"], "held": ["F2"], "objections": {"F3": "理由"}}
    assert parse_selection(v2).verdicts == {"F1": Picked(), "F2": Held(), "F3": Objected("理由")}


@pytest.mark.parametrize(
    ("v2", "number", "kinds"),
    [
        ({"picked": ["F1"], "held": ["F1"], "objections": {}}, "F1", "picked, held"),
        ({"picked": [], "held": ["F2"], "objections": {"F2": "x"}}, "F2", "held, objections"),
        ({"picked": ["F3"], "held": [], "objections": {"F3": "x"}}, "F3", "picked, objections"),
        (
            {"picked": ["F4"], "held": ["F4"], "objections": {"F4": "x"}},
            "F4",
            "picked, held, objections",
        ),
    ],
)
def test_selection_v2_overlap_is_a_read_error_naming_the_number(
    v2: dict[str, object], number: str, kinds: str
) -> None:
    with pytest.raises(ReviewError) as caught:
        parse_selection({"format": 2, **v2})
    assert f"指摘 {number} が複数の扱いに入っています: {kinds}" in str(caught.value)


def test_selection_json_keys_are_derived_from_verdicts() -> None:
    selection = Selection({"F10": Picked(), "F2": Picked(), "F3": Held(), "F4": Objected("理由")})
    assert selection.to_dict() == {
        "picked": ["F2", "F10"],
        "held": ["F3"],
        "objections": {"F4": "理由"},
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


def test_background_v1_old_shape_moves_to_current_keys() -> None:
    v1 = {
        "issues": [],
        "documents": [],
        "past_pull_requests": [{"number": 5, "title": "t", "files": ["x.py"]}],
    }
    background = parse_background(v1)
    assert background.past_changes == (PastPullRequest(5, "t", ("x.py",)),)
    assert background.owners == ()
    assert set(dump_background(background)) == {
        "format",
        "documents",
        "past_changes",
        "owners",
    }


def test_background_v2_drops_issues() -> None:
    v2 = {
        "format": 2,
        "issues": [{"number": 3, "title": "t", "body": "b", "url": "u", "truncated": False}],
        "documents": ["d.md"],
        "past_changes": [],
        "owners": [],
    }
    background = parse_background(v2)
    assert background.documents == ("d.md",)
    assert "issues" not in dump_background(background)
    # 入力は書き換えない
    assert "issues" in v2


def test_background_v1_past_changes_shape() -> None:
    v1 = {
        "issues": [],
        "documents": [],
        "past_changes": [{"kind": "commit", "sha": "a" * 40, "title": "t", "files": []}],
        "owners": [],
    }
    assert parse_background(v1).past_changes == (PastCommit("a" * 40, "t", ()),)


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
