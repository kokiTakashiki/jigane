from pathlib import Path

from jigane.flaky.catalog import load_catalog
from jigane.flaky.detector import detect_source
from jigane.flaky.gates import gate_kind
from jigane.gates.entry import GateKind
from jigane.gates.line_scope import AllLines
from jigane.languages.registry import adapter_for

TESTS = Path(__file__).parent


def test_own_tests_have_no_flaky_violations() -> None:
    """jigane 自身のテストに、jigane の検出器の違反が無い。警告（proxy と判定不能）は数えない。"""
    catalog = {item.id: item for item in load_catalog()}
    found: list[str] = []
    for path in sorted(TESTS.glob("*.py")):
        name = path.as_posix()
        adapter = adapter_for(name)
        assert adapter is not None, name
        detections = detect_source(
            name, path.read_bytes(), adapter, list(catalog.values()), AllLines()
        )
        found.extend(
            f"{detection.item_id} {path.name}:{detection.line} {detection.text}"
            for detection in detections
            if gate_kind(detection, catalog) is GateKind.VIOLATION
        )
    assert found == []
