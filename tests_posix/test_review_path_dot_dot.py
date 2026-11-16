"""まだ無い部分に .. を含むデータの場所を、POSIX で拒むかを確かめる。

Windows の realpath は解決の前に .. を字面で畳むので、.. はまだ無い部分に残らない。
POSIX は部分ごとに辿るので、無い部分の後ろの .. がそのまま残る。
"""

from pathlib import Path

import pytest

from jigane.storage.store import ReviewId, ReviewKind, check_new_review_id


def test_new_review_id_is_refused_when_the_home_has_a_dot_dot_after_a_missing_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "missing" / ".." / "home"))

    with pytest.raises(ValueError, match="データの場所の外"):
        check_new_review_id(ReviewId("octo", "hello", "mine", ReviewKind.RANGE))
