from pathlib import Path

import pytest

from jigane.flaky.catalog import Item, load_catalog
from jigane.flaky.detector import detect_source
from jigane.gates.line_scope import AllLines
from jigane.languages.csharp import CSharpAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "flaky" / "csharp"
CATALOG = load_catalog()
ADAPTER = CSharpAdapter()


def _detect(text: str) -> list[tuple[str, int]]:
    found = detect_source(
        "tests/App.Tests/FooTests.cs", text.encode(), ADAPTER, CATALOG, AllLines()
    )
    return [(d.item_id, d.line) for d in found]


def _csharp_items() -> list[Item]:
    return [i for i in CATALOG if any(q.language == "csharp" for q in i.queries)]


def test_csharp_covers_every_item() -> None:
    assert len(_csharp_items()) == 11


@pytest.mark.parametrize("item", _csharp_items(), ids=lambda i: i.id)
def test_fixtures(item: Item) -> None:
    name = item.id.removeprefix("FLK-").lower().replace("_", "-")
    bad = (FIXTURES / f"{name}-bad.txt").read_text(encoding="utf-8")
    good = (FIXTURES / f"{name}-good.txt").read_text(encoding="utf-8")
    assert item.id in {i for i, _ in _detect(bad)}
    assert _detect(good) == []


@pytest.mark.parametrize(
    ("name", "expected_lines"),
    [
        ("async-wait", {6, 7}),
        ("time", {6, 7, 8, 9}),
        ("random", {6, 7, 8}),
        ("network", {7, 8, 9}),
        ("io", {6, 7, 8}),
        ("unordered", {7, 8}),
        ("float", {6, 7}),
        ("order-dependency", {11, 12, 18, 24, 25, 26}),
        ("resource-leak", {6, 7}),
        ("concurrency", {6, 8, 9}),
    ],
)
def test_bad_fixture_hits_every_line(name: str, expected_lines: set[int]) -> None:
    bad = (FIXTURES / f"{name}-bad.txt").read_text(encoding="utf-8")
    item_id = f"FLK-{name.upper()}"
    lines = {line for found_id, line in _detect(bad) if found_id == item_id}
    assert lines == expected_lines


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ('[Fact(Skip = "なぜ失敗するのか?")]', False),
        ('[Fact(Skip = "reason")]', False),
        ('[Fact(Skip = Env.CI ? "ci" : null)]', True),
    ],
)
def test_conditional_skip_attribute_looks_at_expression(attribute: str, *, expected: bool) -> None:
    code = f"class FooTests\n{{\n    {attribute}\n    public void A() {{ }}\n}}\n"
    assert (("FLK-SKIP-CONDITIONAL", 3) in _detect(code)) is expected
