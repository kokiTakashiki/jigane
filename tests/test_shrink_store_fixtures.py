import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent


def load_shrinker() -> ModuleType:
    path = ROOT / "scripts" / "shrink_store_fixtures.py"
    spec = importlib.util.spec_from_file_location("shrink_store_fixtures", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
