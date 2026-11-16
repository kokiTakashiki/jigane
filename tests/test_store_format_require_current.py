"""require_current_version の単体の試験。"""

import pytest

from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import (
    REVIEW_REBUILD_HINT,
    OlderFormatError,
    require_current_version,
)


def test_a_current_version_returns_the_dict_without_format() -> None:
    raw = {"format": 3, "a": 1}
    assert require_current_version("x.json", raw, current=3) == {"a": 1}
    assert raw == {"format": 3, "a": 1}


def test_an_older_version_raises_the_older_format_error_with_file_version_and_current() -> None:
    with pytest.raises(OlderFormatError) as caught:
        require_current_version("x.json", {"format": 2}, current=3)
    message = str(caught.value)
    assert "x.json の構造のバージョンは 2" in message
    assert "読めるのは 3 だけ" in message
    assert message.endswith(REVIEW_REBUILD_HINT)
    assert isinstance(caught.value, ReviewError)


def test_a_missing_format_is_version_1() -> None:
    require_current_version("x.json", {"a": 1}, current=1)
    with pytest.raises(OlderFormatError, match="バージョンは 1 "):
        require_current_version("x.json", {"a": 1}, current=2)


def test_the_rebuild_hint_is_replaceable() -> None:
    with pytest.raises(OlderFormatError, match="別の作り直し方"):
        require_current_version("x.json", {"format": 1}, current=2, rebuild_hint="別の作り直し方")


def test_newer_and_invalid_versions_are_plain_review_errors() -> None:
    with pytest.raises(ReviewError, match="新しいバージョン") as newer:
        require_current_version("x.json", {"format": 4}, current=3)
    assert not isinstance(newer.value, OlderFormatError)
    with pytest.raises(ReviewError, match="整数ではありません") as invalid:
        require_current_version("x.json", {"format": "3"}, current=3)
    assert not isinstance(invalid.value, OlderFormatError)
