from types import ModuleType

from script_loader import import_script


def load_shrinker() -> ModuleType:
    return import_script("shrink_store_fixtures")


def test_shrink_keeps_every_line_counts_key() -> None:
    line_counts = {
        "by_kind": {"test": 1, "logic": 2, "generated": 3, "format_only": 4},
        "by_level": {"required": 1, "confirm": 2, "machine": 3, "extra": 4},
        "kind_labels": {"a": "x", "b": "y", "c": "z", "d": "w"},
        "kind_levels": {"a": "required", "b": "confirm", "c": "machine", "d": "confirm"},
    }
    shrunk = load_shrinker().shrink({"line_counts": line_counts})
    assert shrunk == {"line_counts": line_counts}


def test_shrink_still_merges_same_shaped_regions() -> None:
    regions = {f"r{i}": {"line_count": i} for i in range(5)}
    shrunk = load_shrinker().shrink({"regions": regions})
    assert list(shrunk["regions"]) == ["r0"]
