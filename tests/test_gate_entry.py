"""gates.json の一件の型が、保存の形と往復し、古い形を読み、終了コードの判定を持つこと。"""

import json
from pathlib import Path
from typing import Any

import pytest
from gates_support import write_gates_file
from store_format_support import check_upgrades

from jigane.flaky.catalog import load_catalog
from jigane.flaky.gates import _UPGRADES, GATES_FILE, load_gates, update_gates
from jigane.gates.entry import (
    GATES_FORMAT,
    Active,
    Allowed,
    FlakyDetail,
    GateEntry,
    GateKind,
    Gates,
    GateSeverity,
    GateSource,
    NumberAllocator,
    NumberLedger,
    RegexDetail,
)
from jigane.storage.review_error import ReviewError

FLAKY_SOURCE = "import time\n\n\ndef test_a():\n    time.sleep(1)\n"
REGEX_SOURCE = 'import re\n\nPATTERN = re.compile(r"(a+)+$")\n'
NIT_SOURCE = 'import re\n\nTEXT = re.sub("abc", "", "xabc")\n'


def _saved(tmp_path: Path, name: str, *, with_base: bool) -> Gates:
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_a.py").write_text(FLAKY_SOURCE + REGEX_SOURCE, encoding="utf-8")
    out = tmp_path / name
    out.mkdir()
    base = tmp_path / "base"
    base.mkdir(exist_ok=True)
    # 変更前の木を渡すと flaky は変更行だけ、正規表現は変更した札だけ。渡さなければ flaky だけ
    return update_gates(
        out,
        tmp_path,
        ["tests/test_a.py"],
        [],
        load_catalog(),
        renames={},
        base_root=base if with_base else None,
    )


def test_saved_file_round_trips_through_the_type(tmp_path: Path) -> None:
    flaky = _saved(tmp_path, "flaky", with_base=False)
    regex = _saved(tmp_path, "regex", with_base=True)
    assert {e.source for e in flaky.violations} == {GateSource.FLAKY}
    assert {e.source for e in regex.violations} == {GateSource.REGEX}
    for name, gates in (("flaky", flaky), ("regex", regex)):
        assert load_gates(tmp_path / name) == gates
        saved = json.loads((tmp_path / name / GATES_FILE).read_text(encoding="utf-8"))
        assert saved["violations"] == gates.to_json()["violations"]
        for entry in gates.violations:
            assert GateEntry.from_json(entry.to_json()) == entry


def test_json_keeps_the_keys_readers_use(tmp_path: Path) -> None:
    flaky = _saved(tmp_path, "flaky", with_base=False).to_json()["violations"][0]
    regex = _saved(tmp_path, "regex", with_base=True).to_json()["violations"][0]
    assert {"number", "id", "path", "line", "excerpt", "explanation", "reason", "cause"} <= set(
        flaky
    )
    assert flaky["source"] == "flaky"
    assert "severity" not in flaky
    assert (regex["source"], regex["severity"]) == ("regex", "warning")


V1_KEYS = [
    "cause", "confidence", "detector_note", "excerpt", "id", "legitimate",
    "line", "number", "path", "reason", "remedy", "source",
]  # fmt: skip
CITATION = "jigane: allow-flaky の注記の書式"


def _v1_entry() -> dict[str, Any]:
    entry: dict[str, Any] = {
        "number": "G1",
        "id": "FLK-ASYNC-WAIT",
        "path": "tests/test_a.py",
        "line": 5,
        "excerpt": "time.sleep(1)",
        "reason": "r",
        "cause": "c",
        "confidence": "direct",
        "legitimate": "",
        "detector_note": "",
        "remedy": "知らない鍵は読み捨てる",
        "source": CITATION,
    }
    return entry


def _write(tmp_path: Path, state: dict[str, Any]) -> None:
    write_gates_file(tmp_path, state)


def test_version_1_file_with_the_real_key_order_is_read_and_rewritten_as_version_2(
    tmp_path: Path,
) -> None:
    entry = _v1_entry()
    assert sorted(entry) == V1_KEYS
    _write(tmp_path, {"violations": [entry], "warnings": []})
    gates = load_gates(tmp_path)
    assert gates is not None
    loaded = gates.violations[0]
    assert loaded.source is GateSource.FLAKY
    assert isinstance(loaded.detail, FlakyDetail)
    assert loaded.detail.origin == CITATION
    assert loaded.explanation  # 目録を引いて補う
    saved = loaded.to_json()
    assert (saved["source"], saved["origin"]) == ("flaky", CITATION)
    assert "remedy" not in saved
    assert gates.to_json()["format"] == 2


def test_version_1_entry_missing_fields_is_filled_from_the_catalog(tmp_path: Path) -> None:
    item = next(i for i in load_catalog() if i.id == "FLK-ASYNC-WAIT")
    keep = ("number", "id", "path", "line", "excerpt")
    bare = {k: v for k, v in _v1_entry().items() if k in keep}
    _write(tmp_path, {"violations": [bare], "warnings": []})
    gates = load_gates(tmp_path)
    assert gates is not None
    detail = gates.violations[0].detail
    assert isinstance(detail, FlakyDetail)
    assert (detail.cause, detail.confidence, detail.legitimate) == (
        item.cause,
        item.confidence,
        item.legitimate,
    )
    assert (detail.origin, detail.reason, detail.detector_note) == (item.source, "", "")
    assert detail.equivalent is None


def test_version_1_entry_of_an_unknown_id_gets_empty_defaults(tmp_path: Path) -> None:
    bare = {"number": "G1", "id": "FLK-GONE", "path": "a.py", "line": 1, "excerpt": "x"}
    _write(tmp_path, {"violations": [bare], "warnings": []})
    gates = load_gates(tmp_path)
    assert gates is not None
    assert gates.violations[0].detail == FlakyDetail("", "", "", "", "")


def test_version_2_entry_with_an_invalid_source_is_an_error(tmp_path: Path) -> None:
    detail = FlakyDetail("", "c", "d", "o", "")
    entry = GateEntry("G1", "X", "a.py", 1, "x", {}, GateSeverity.FLAKY, detail)
    broken = {**entry.to_json(), "source": CITATION}
    _write(tmp_path, {"format": 2, "violations": [broken], "warnings": []})
    with pytest.raises(ValueError, match="flaky"):
        load_gates(tmp_path)


def test_unknown_version_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, {"format": 3, "violations": [], "warnings": []})
    with pytest.raises(ReviewError, match="版"):
        load_gates(tmp_path)


def _entry(severity: GateSeverity) -> GateEntry:
    detail = (
        FlakyDetail("", "c", "direct", "o", "")
        if severity is GateSeverity.FLAKY
        else RegexDetail()
    )
    return GateEntry("G1", "X", "a.py", 1, "x", {}, severity, detail)


@pytest.mark.parametrize(
    "severity", [GateSeverity.FLAKY, GateSeverity.REGEX_WARNING, GateSeverity.REGEX_NIT]
)
def test_an_active_entry_counts_whatever_its_severity(severity: GateSeverity) -> None:
    entry = _entry(severity)
    assert entry.blocks_exit
    assert entry.state == Active()
    assert Gates((entry,)).blocks_exit
    # 警告の列は終了コードに数えない
    assert not Gates((), (entry,)).blocks_exit


def _allowed(severity: GateSeverity = GateSeverity.REGEX_NIT) -> GateEntry:
    return GateEntry(
        "G1", "R6", "a.py", 1, "x", {}, severity, RegexDetail(), Allowed("外部の仕様のため")
    )


def test_an_allowed_regex_entry_does_not_block_and_round_trips() -> None:
    entry = _allowed()
    assert not entry.blocks_exit
    assert entry.state == Allowed("外部の仕様のため")
    assert not Gates((entry,)).blocks_exit
    assert entry.to_json()["allowed"] == "外部の仕様のため"
    assert GateEntry.from_json(entry.to_json()) == entry
    assert "allowed" not in _entry(GateSeverity.REGEX_NIT).to_json()


def test_counts_separate_allowed_from_counted_violations() -> None:
    gates = Gates((_entry(GateSeverity.REGEX_WARNING), _allowed(), _allowed()))
    assert (gates.counted_count, gates.allowed_count) == (1, 2)
    assert gates.blocks_exit
    assert not Gates((_allowed(),)).blocks_exit
    assert Gates.from_json(gates.to_json()) == gates


def test_a_gates_json_without_the_allowed_key_reads_as_active() -> None:
    data = _entry(GateSeverity.REGEX_NIT).to_json()
    assert "allowed" not in data
    assert GateEntry.from_json(data).state == Active()


def test_a_flaky_entry_cannot_be_allowed() -> None:
    with pytest.raises(ValueError, match="外せません"):
        GateEntry(
            "G1", "X", "a.py", 1, "x", {}, GateSeverity.FLAKY,
            FlakyDetail("", "c", "direct", "o", ""), Allowed("理由"),
        )  # fmt: skip


def test_severity_and_detail_must_agree() -> None:
    with pytest.raises(ValueError, match="固有の欄"):
        GateEntry(
            "G1", "X", "a.py", 1, "x", {}, GateSeverity.REGEX_NIT, FlakyDetail("", "", "", "", "")
        )


def test_ledger_numbers_each_kind_in_its_own_sequence() -> None:
    allocator = NumberLedger().allocator()
    a = NumberLedger.base_key("a.py", "X", "one")
    b = NumberLedger.base_key("a.py", "X", "two")
    assert allocator.assign(a, GateKind.VIOLATION) == "G1"
    assert allocator.assign(b, GateKind.WARNING) == "W1"
    assert allocator.assign(NumberLedger.base_key("b.py", "Y", "x"), GateKind.VIOLATION) == "G2"
    ledger = allocator.settle()
    assert (ledger.next_violation, ledger.next_warning) == (3, 2)


def test_ledger_numbers_identical_detections_by_occurrence() -> None:
    allocator = NumberLedger().allocator()
    base = NumberLedger.base_key("a.py", "X", "same")
    assert [allocator.assign(base, GateKind.VIOLATION) for _ in range(3)] == ["G1", "G2", "G3"]


def test_ledger_carries_numbers_and_never_reuses_them() -> None:
    base = NumberLedger.base_key("a.py", "X", "one")
    other = NumberLedger.base_key("a.py", "X", "other")
    first = NumberLedger().allocator()
    assert first.assign(base, GateKind.VIOLATION) == "G1"
    carried = first.settle()
    second = carried.allocator()
    assert second.assign(other, GateKind.VIOLATION) == "G2"
    assert second.assign(base, GateKind.VIOLATION) == "G1"
    settled = second.settle()
    assert settled.next_violation == 3
    assert carried.next_violation == 2  # 確定した台帳は、続きの割り当てで動かない
    assert NumberLedger.from_json(settled.to_json()) == settled


def test_ledger_renumbers_when_the_kind_changes() -> None:
    base = NumberLedger.base_key("a.py", "X", "one")
    first = NumberLedger().allocator()
    assert first.assign(base, GateKind.VIOLATION) == "G1"
    assert first.settle().allocator().assign(base, GateKind.WARNING) == "W1"


def test_a_ledger_cannot_be_changed_after_it_is_made() -> None:
    ledger = NumberLedger(2, 1, {"k": "G1"})
    with pytest.raises(AttributeError):
        ledger.next_violation = 9  # type: ignore[misc]
    with pytest.raises(TypeError):
        ledger.keys["x"] = "G2"  # type: ignore[index]


@pytest.mark.parametrize("lost", ["next_violation", "next_warning", "keys"])
def test_a_gates_json_without_a_ledger_key_is_an_error(tmp_path: Path, lost: str) -> None:
    state = {**NumberLedger().to_json(), "format": 2, "violations": [], "warnings": []}
    del state[lost]
    (tmp_path / GATES_FILE).write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match=lost):
        load_gates(tmp_path)


def test_every_source_takes_its_number_from_the_one_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    assign = NumberAllocator.assign

    def spy(self: NumberAllocator, base_key: str, kind: GateKind) -> str:
        calls.append(base_key)
        return assign(self, base_key, kind)

    monkeypatch.setattr(NumberAllocator, "assign", spy)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(FLAKY_SOURCE + REGEX_SOURCE, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    paths = ["tests/test_a.py"]
    # base_root を渡さない回は flaky だけ、渡す回は正規表現の札だけを検出する
    flaky = update_gates(out, tmp_path, paths, [], load_catalog(), renames={})
    regex = update_gates(out, tmp_path, paths, [], load_catalog(), base_root=base, renames={})
    assert {e.source for e in flaky.violations} == {GateSource.FLAKY}
    assert {e.source for e in regex.violations} == {GateSource.REGEX}
    assert len(calls) == len(flaky.violations) + len(flaky.warnings) + len(regex.violations)
    flaky_numbers = {e.number for e in flaky.violations}
    regex_numbers = {e.number for e in regex.violations}
    assert flaky_numbers.isdisjoint(regex_numbers)


def test_numbers_are_carried_from_a_version_1_file(tmp_path: Path) -> None:
    first = _saved(tmp_path, "first", with_base=True)
    saved = json.loads((tmp_path / "first" / GATES_FILE).read_text(encoding="utf-8"))
    bumped = {key: f"G{int(num[1:]) + 10}" for key, num in saved["keys"].items()}
    v1 = {
        "next_violation": 50,
        "next_warning": 1,
        "keys": bumped,
        "violations": [],
        "warnings": [],
    }
    out = tmp_path / "second"
    out.mkdir()
    _write(out, v1)
    second = update_gates(
        out,
        tmp_path,
        ["tests/test_a.py"],
        [],
        load_catalog(),
        renames={},
        base_root=tmp_path / "base",
    )
    assert [e.number for e in second.violations] == [
        f"G{int(e.number[1:]) + 10}" for e in first.violations
    ]
    assert json.loads((out / GATES_FILE).read_text(encoding="utf-8"))["format"] == 2
    assert second.ledger.next_violation == 50


def test_upgrade_table_has_every_step() -> None:
    check_upgrades(GATES_FILE, GATES_FORMAT, _UPGRADES)
