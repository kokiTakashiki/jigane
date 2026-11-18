import pytest

from jigane.publishing.rendering import find_template
from jigane.reviews.lifecycle import ReviewError
from jigane.storage.store import ReviewId

TRAILING = ["\n", " ", "\t", "\r\n"]


@pytest.mark.parametrize("suffix", TRAILING)
def test_review_id_parse_rejects_trailing_whitespace(suffix: str) -> None:
    assert str(ReviewId.parse("octo/hello@scratch")) == "octo/hello@scratch"
    with pytest.raises(ValueError, match="識別子の形が不正"):
        ReviewId.parse(f"octo/hello@scratch{suffix}")


@pytest.mark.parametrize("suffix", TRAILING)
def test_review_id_parse_short_rejects_trailing_whitespace(suffix: str) -> None:
    ReviewId.parse_short("hello@scratch")
    with pytest.raises(ValueError, match="識別子の形が不正"):
        ReviewId.parse_short(f"hello@scratch{suffix}")


def test_template_name_rejects_a_trailing_newline() -> None:
    with pytest.raises(ReviewError, match="テンプレートの名前が不正です"):
        find_template("default\n")
