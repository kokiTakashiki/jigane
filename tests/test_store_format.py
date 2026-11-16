import pytest

from jigane.storage.store_format import FormatState, classify_version, stored_version


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, FormatState.OLDER),
        ({"format": 1}, FormatState.OLDER),
        ({"format": 3}, FormatState.CURRENT),
        ({"format": 4}, FormatState.NEWER),
        ({"format": 0}, FormatState.INVALID),
        ({"format": "3"}, FormatState.INVALID),
        ({"format": True}, FormatState.INVALID),
        ([], FormatState.OLDER),
    ],
)
def test_classify_version_by_stored_version(raw: object, expected: FormatState) -> None:
    assert classify_version(stored_version(raw), 3) is expected
