"""findings.json の型の往復と、旧形式の移行が読み込みの一度で済むことを確かめる。"""

import json
from pathlib import Path
from typing import Any

import pytest
from ast_support import edits_state_by_key, json_users_of_stored_file, reads_or_writes_stored_file
from review_fakes import group_result, stored_finding

from jigane.findings.records import (
    FINDINGS_FILE,
    DeepeningBody,
    FindingBody,
    FindingKind,
    RegularBody,
    ScopeBody,
    StoredFinding,
    Triage,
    load_findings_state,
    mark_stale,
    merge_group,
    read_findings_json,
    read_stored_state,
    reserve_numbers,
    save_findings_state,
)
from jigane.groups.definitions import MACRO, MICRO


def _finding(finding_id: str, triage: object, severity: str = "major") -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "viewpoint": "V",
        "claim": "C",
        "triage": triage,
        "theme": "T1",
        "adjudication": None,
    }


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    state = {
        "last_number": 3,
        "groups": {
            "micro": [
                _finding("F1", "no-such-name"),
                _finding("F2", "deferred"),
                _finding("F3", None, "nit"),
            ]
        },
        "themes": [{"id": "T1", "finding_ids": ["F1"], "triage": "legacy-theme-name"}],
    }
    (tmp_path / FINDINGS_FILE).write_text(json.dumps(state), encoding="utf-8")
    return tmp_path


def _stored(directory: Path) -> dict[str, Any]:
    stored: dict[str, Any] = json.loads((directory / FINDINGS_FILE).read_text(encoding="utf-8"))
    return stored


def _stored_triages(directory: Path) -> list[object]:
    state = _stored(directory)
    return [f["triage"] for f in state["groups"]["micro"]] + [state["themes"][0]["triage"]]


# 読み込みで一度だけ移行された後の保存値。古い名前は現行の名前になり、知らない名前は区分なしになる
EXPECTED = [None, "remaining", None, None]
# 読むだけでは findings.json を書き換えない
ORIGINAL = ["no-such-name", "deferred", None, "legacy-theme-name"]


def test_reader_sees_filled_triage(directory: Path) -> None:
    state = load_findings_state(directory)
    micro = state.groups[MICRO]
    assert micro[0].decision.triage is Triage.MUST_READ
    assert micro[1].decision.triage is Triage.REMAINING
    assert micro[2].decision.triage is Triage.FOLDED
    assert state.themes[0].triage is Triage.MUST_READ
    assert _stored_triages(directory) == ORIGINAL


def test_reserve_numbers_keeps_stored_triage(directory: Path) -> None:
    assert reserve_numbers(directory, 2) == 4
    assert _stored(directory)["last_number"] == 5
    assert _stored_triages(directory) == EXPECTED


def test_merge_group_keeps_other_groups_stored_triage(directory: Path) -> None:
    merge_group(directory, MACRO, group_result([_finding("F9", "must_read")]), None)
    assert _stored_triages(directory) == EXPECTED
    assert _stored(directory)["groups"]["macro"][0]["triage"] == "must_read"


def test_mark_stale_keeps_stored_triage(directory: Path) -> None:
    mark_stale(directory)
    assert _stored(directory)["stale_groups"] == ["micro"]
    assert _stored_triages(directory) == EXPECTED


def test_writer_read_then_save_keeps_stored_triage(directory: Path) -> None:
    # 書き手の流れ。別の群を読み直して差し替え、保存しても、
    # micro の区分は移行後の値のまま変わらない
    state = read_stored_state(directory)
    state.groups[MACRO] = (stored_finding(_finding("F4", "must_read")),)
    save_findings_state(directory, state)
    assert _stored_triages(directory) == EXPECTED


def test_stored_findings_round_trips_the_current_form() -> None:
    raw = {
        "format": 3,
        "last_number": 7,
        "groups": {"macro": [_finding("F1", "must_read")]},
        "stale_groups": ["macro"],
        "last_theme_number": 2,
        "themes": [
            {
                "id": "B1",
                "title": "t",
                "finding_ids": ["F1"],
                "severity": "warning",
                "triage": "must_read",
                "other": 1,
            }
        ],
    }
    stored = read_findings_json(raw)
    assert stored.last_number == 7
    assert stored.stale_groups == (MACRO,)
    assert json.loads(json.dumps(stored.to_json())) == raw


def test_read_findings_json_does_not_touch_the_input() -> None:
    raw: dict[str, Any] = {"last_number": 1, "groups": {"macro": [_finding("F1", "deferred")]}}
    read_findings_json(raw)
    assert raw["groups"]["macro"][0]["triage"] == "deferred"


def test_read_findings_json_fills_defaults_for_the_oldest_form() -> None:
    stored = read_findings_json({"last_number": 1, "groups": {}})
    assert (stored.stale_groups, stored.last_theme_number, stored.themes) == ((), 0, ())


def test_read_findings_json_moves_theme_triage_to_findings_without_one() -> None:
    raw = {
        "last_number": 1,
        "groups": {"micro": [_finding("F1", None)]},
        "themes": [{"id": "T1", "finding_ids": ["F1"], "triage": "remaining"}],
    }
    assert read_findings_json(raw).groups[MICRO][0].triage is Triage.REMAINING


def test_read_findings_json_migrates_legacy_nit_in_a_group() -> None:
    legacy = {
        "id": "F1",
        "severity": "nit",
        "viewpoint": "discarded",
        "claim": "A",
        "discard_kind": "other",
        "concern": "C",
    }
    raw: dict[str, Any] = {"last_number": 1, "groups": {"micro": [legacy]}}
    migrated = read_findings_json(raw).groups[MICRO][0].to_json()
    assert "discard_kind" not in migrated
    assert "concern" not in migrated
    assert migrated["nit_reason"]["kind"] == "other"
    assert raw["groups"]["micro"][0] == legacy


def test_only_findings_module_reads_or_writes_the_findings_file() -> None:
    root = Path(__file__).parent.parent / "jigane"
    handlers = json_users_of_stored_file(root, "FINDINGS_FILE", "findings.json")
    assert handlers == ["findings/records.py"]


def test_writers_outside_findings_do_not_edit_the_state_by_key() -> None:
    root = Path(__file__).parent.parent / "jigane"
    keys = frozenset({"groups", "themes", "stale_groups", "last_number", "last_theme_number"})
    editors = [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*.py"))
        if path.relative_to(root).as_posix() != "findings/records.py"
        and edits_state_by_key(path.read_text(encoding="utf-8"), keys)
    ]
    assert editors == []


def test_a_write_by_key_to_the_state_is_found() -> None:
    keys = frozenset({"groups", "last_number"})
    for source in (
        'state["groups"] = {}\n',
        'state["groups"]["micro"] = []\n',
        'state["last_number"] += 1\n',
        "state['last_number']=1\n",
        'state["groups"], x = 1, 2\n',
        'x, [y, *state["groups"]] = 1, 2, 3\n',
    ):
        assert edits_state_by_key(source, keys), source
    for source in ('x = state["groups"]\n', 'other["groups"] = 1\n', 'state["format"] = 1\n'):
        assert not edits_state_by_key(source, keys), source


def test_a_direct_json_read_or_write_of_the_findings_file_is_found() -> None:
    for source in (
        "import json\njson.loads(open(FINDINGS_FILE).read())\n",
        'import json\njson.dumps(x)\np = "findings.json"\n',
    ):
        assert reads_or_writes_stored_file(source, "FINDINGS_FILE", "findings.json"), source
    for source in (
        'FINDINGS_FILE = "findings.json"\nimport json\njson.dumps(x)\n',
        "import json\njson.dumps(x)\n",
        "x = FINDINGS_FILE\n",
    ):
        assert not reads_or_writes_stored_file(source, "FINDINGS_FILE", "findings.json"), source


def test_a_group_name_outside_the_current_groups_is_kept_and_written_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    legacy = [{"id": "F9", "triage": "banana"}]
    raw = {
        "format": 2,
        "last_number": 1,
        "groups": {"macro": [_finding("F1", "must_read")], "retired": legacy},
        "stale_groups": [],
        "last_theme_number": 0,
        "themes": [],
    }
    with caplog.at_level("WARNING"):
        stored = read_findings_json(raw)
    assert list(stored.groups) == [MACRO]
    assert stored.unknown_groups == {"retired": legacy}
    assert "retired" in caplog.text
    assert stored.to_json()["groups"] == raw["groups"]


def test_stored_finding_round_trips_and_keeps_extra_fields() -> None:
    raw = {
        **_finding("F1", "must_read"),
        "evidence": [{"path": "a.py", "start_line": 1, "end_line": 2}],
        "unknown_field": {"nested": [1]},
    }
    parsed = StoredFinding.from_json(raw)
    assert parsed.triage is Triage.MUST_READ
    assert parsed.body.extra["unknown_field"] == {"nested": [1]}
    assert parsed.to_json() == raw


def test_stored_finding_omits_absent_optional_fields() -> None:
    written = StoredFinding.from_json(_finding("F1", "remaining")).to_json()
    assert "deepens" not in written
    assert "out_of_scope" not in written
    assert "nit_reason" not in written


_SCOPE = {"paths": ["a.py"], "reason": "R"}


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, RegularBody),
        ({"deepens": "F1"}, DeepeningBody),
        ({"out_of_scope": _SCOPE}, ScopeBody),
        ({"out_of_scope": _SCOPE, "deepens": "F1"}, ScopeBody),
    ],
)
def test_stored_finding_kind_is_decided_once_by_from_json(
    extra: dict[str, Any], expected: type[FindingBody]
) -> None:
    raw = {**_finding("F2", "remaining"), **extra}
    parsed = StoredFinding.from_json(raw)
    assert type(parsed.body) is expected
    assert parsed.to_json() == raw


def test_stored_finding_body_carries_the_kind_fields() -> None:
    scope = StoredFinding.from_json({**_finding("F3", None), "out_of_scope": _SCOPE}).body
    assert isinstance(scope, ScopeBody)
    assert scope.out_of_scope == _SCOPE
    deepening = StoredFinding.from_json({**_finding("F2", None), "deepens": "F1"}).body
    assert isinstance(deepening, DeepeningBody)
    assert deepening.continued_from == "F1"


def test_stored_finding_kind_follows_the_type() -> None:
    kinds = [
        StoredFinding.from_json(_finding("F1", None)).body.kind,
        StoredFinding.from_json({**_finding("F2", None), "deepens": "F1"}).body.kind,
        StoredFinding.from_json({**_finding("F3", None), "out_of_scope": _SCOPE}).body.kind,
    ]
    assert kinds == [FindingKind.REGULAR, FindingKind.DEEPENING, FindingKind.SCOPE]


_COMMON: dict[str, Any] = {
    "id": "F2",
    "severity": "major",
    "viewpoint": "v",
    "claim": "c",
    "theme": None,
    "adjudication": None,
    "nit_reason": None,
}


def test_regular_finding_cannot_hold_a_continued_from() -> None:
    with pytest.raises(TypeError, match="continued_from"):
        RegularBody(**_COMMON, continued_from="F1")  # type: ignore[call-arg]


def test_deepening_finding_requires_a_continued_from() -> None:
    with pytest.raises(TypeError, match="continued_from"):
        DeepeningBody(**_COMMON)


@pytest.mark.parametrize("continued_from", [None, "F1"])
def test_scope_finding_accepts_either_continued_from(continued_from: str | None) -> None:
    finding = ScopeBody(**_COMMON, out_of_scope=_SCOPE, continued_from=continued_from)
    assert finding.continued_from == continued_from
