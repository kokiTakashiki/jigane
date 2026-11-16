from jigane.judgment_map.allow_annotation import AllowedSpot, AllowScan


def test_permits_and_reason_for_are_keyed_by_rule_and_line() -> None:
    scan = AllowScan(allowed={AllowedSpot("F1", 3): "理由"})
    assert scan.permits("F1", 3)
    assert scan.reason_for("F1", 3) == "理由"
    assert not scan.permits("F1", 4)
    assert not scan.permits("F2", 3)
    assert scan.reason_for("F2", 3) is None
