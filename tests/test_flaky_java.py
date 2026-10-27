from pathlib import Path

import pytest

from jigane.flaky.catalog import Item, load_catalog
from jigane.flaky.detector import detect_source
from jigane.gates.line_scope import AllLines
from jigane.languages.java import JavaAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "flaky" / "java"
CATALOG = load_catalog()
# Python だけが検出する型。他の言語の query はまだ無い
PYTHON_ONLY = frozenset({"FLK-WALL-CLOCK"})
JAVA_ITEMS = [i for i in CATALOG if i.id not in PYTHON_ONLY]
ADAPTER = JavaAdapter()
BAD_MARKER = "// bad"


def _detect(text: str) -> list[tuple[str, int]]:
    found = detect_source(
        "src/test/java/FooTest.java", text.encode(), ADAPTER, CATALOG, AllLines()
    )
    return [(d.item_id, d.line) for d in found]


def _name(item: Item) -> str:
    return item.id.removeprefix("FLK-").lower()


@pytest.mark.parametrize("item", JAVA_ITEMS, ids=lambda i: i.id)
def test_java_query_exists(item: Item) -> None:
    assert any(q.language == "java" for q in item.queries)


@pytest.mark.parametrize("item", JAVA_ITEMS, ids=lambda i: i.id)
def test_java_fixtures(item: Item) -> None:
    name = _name(item)
    bad = (FIXTURES / f"{name}-bad.txt").read_text(encoding="utf-8")
    good = (FIXTURES / f"{name}-good.txt").read_text(encoding="utf-8")
    marked = [n for n, line in enumerate(bad.splitlines(), 1) if BAD_MARKER in line]
    assert marked
    found = sorted(line for item_id, line in _detect(bad) if item_id == item.id)
    assert found == marked
    assert _detect(good) == []
