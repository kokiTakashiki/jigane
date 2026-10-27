from pathlib import Path
from typing import Any

from jigane.flaky.catalog import Item, load_catalog
from jigane.flaky.gates import update_gates

CATALOG = load_catalog()
CSHARP_LEAK = (
    "public class FooTests\n{\n    [Fact]\n    public void Opens()\n    {\n"
    "        var fs = new FileStream(path, FileMode.Open);\n    }\n}\n"
)
PYTHON_SLEEP = "import time\n\n\ndef test_a():\n    time.sleep(1)\n"


def _violation(tmp_path: Path, path: str, code: str) -> dict[str, Any]:
    (tmp_path / Path(path).parent).mkdir(parents=True)
    (tmp_path / path).write_text(code, encoding="utf-8")
    (tmp_path / "out").mkdir()
    gates = update_gates(tmp_path / "out", tmp_path, [path], [], CATALOG, renames={})
    found = gates.violations + gates.warnings
    assert len(found) == 1
    return found[0].to_json()


def _item(item_id: str) -> Item:
    return next(i for i in CATALOG if i.id == item_id)


def test_query_note_and_direction_reach_the_entry(tmp_path: Path) -> None:
    entry = _violation(tmp_path, "tests/App.Tests/FooTests.cs", CSHARP_LEAK)
    spec = next(q for q in _item("FLK-RESOURCE-LEAK").queries if q.language == "csharp")
    assert spec.note is not None
    assert spec.direction is not None
    assert entry["legitimate"] == _item("FLK-RESOURCE-LEAK").legitimate
    assert entry["detector_note"] == spec.note
    assert entry["explanation"]["direction"] == spec.direction
    assert spec.direction != _item("FLK-RESOURCE-LEAK").explanation.direction


def test_item_text_is_used_without_query_override(tmp_path: Path) -> None:
    entry = _violation(tmp_path, "tests/test_a.py", PYTHON_SLEEP)
    spec = next(q for q in _item("FLK-ASYNC-WAIT").queries if q.language == "python")
    assert spec.note is None
    assert spec.direction is None
    assert entry["legitimate"] == _item("FLK-ASYNC-WAIT").legitimate
    assert entry["detector_note"] == ""
    assert entry["explanation"]["direction"] == _item("FLK-ASYNC-WAIT").explanation.direction


def test_equivalent_of_the_detected_language_reaches_the_entry(tmp_path: Path) -> None:
    entry = _violation(tmp_path, "tests/test_a.py", PYTHON_SLEEP)
    assert entry["equivalent"]["state"] == "found"
    assert entry["equivalent"]["rules"] == ["ruff ASYNC251"]
    entry = _violation(tmp_path / "cs", "tests/App.Tests/FooTests.cs", CSHARP_LEAK)
    assert entry["equivalent"] == {"state": "unverified", "rules": [], "note": ""}
