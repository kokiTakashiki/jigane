from pathlib import Path

import pytest

from jigane.flaky.catalog import Item, load_catalog
from jigane.flaky.detector import detect_source
from jigane.gates.line_scope import AllLines
from jigane.languages.swift import SwiftAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "flaky" / "swift"
CATALOG = load_catalog()
# Python だけが検出する型。他の言語の query はまだ無い
PYTHON_ONLY = frozenset({"FLK-WALL-CLOCK"})
ADAPTER = SwiftAdapter()
SWIFT_ITEMS = [i for i in CATALOG if any(q.language == "swift" for q in i.queries)]


def _detect(text: str) -> list[tuple[str, int]]:
    found = detect_source(
        "Tests/AppTests/FooTests.swift", text.encode(), ADAPTER, CATALOG, AllLines()
    )
    return [(d.item_id, d.line) for d in found]


@pytest.mark.parametrize("item", SWIFT_ITEMS, ids=lambda i: i.id)
def test_fixtures(item: Item) -> None:
    name = item.id.removeprefix("FLK-").lower()
    bad = (FIXTURES / f"{name}-bad.txt").read_text(encoding="utf-8")
    good = (FIXTURES / f"{name}-good.txt").read_text(encoding="utf-8")
    assert item.id in {i for i, _ in _detect(bad)}
    assert _detect(good) == []


def test_every_item_has_a_swift_query() -> None:
    assert len(SWIFT_ITEMS) == len(CATALOG) - len(PYTHON_ONLY)


def test_bad_fixtures_hit_each_pattern() -> None:
    # 各 bad の fixture は、書き方の違う例を複数持つ。行ごとに当たることを確かめる
    expected = {
        "async-wait": 3,
        "time": 3,
        "random": 3,
        "network": 2,
        "io": 2,
        "unordered": 2,
        "float": 2,
        "skip-conditional": 5,
        "order-dependency": 2,
        "concurrency": 2,
    }
    for name, count in expected.items():
        item_id = f"FLK-{name.upper()}"
        found = [
            line
            for i, line in _detect((FIXTURES / f"{name}-bad.txt").read_text(encoding="utf-8"))
            if i == item_id
        ]
        assert len(found) == count, name


def test_xctest_expectation_and_confirmation_are_not_async_wait() -> None:
    assert (
        _detect("final class FooTests {\n    func testA() { wait(for: [e], timeout: 1) }\n}\n")
        == []
    )
