import re

import pytest

from jigane.flaky.catalog import load_catalog

POLITE_ENDINGS = ("ます。", "です。", "ません。", "ました。", "でした。", "ください。")


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=。)", text) if part]


def _shown_texts() -> list[tuple[str, str]]:
    """返信とレポートに出る source と note。"""
    texts: list[tuple[str, str]] = []
    for item in load_catalog():
        texts.append((f"{item.id}/source", item.source))
        for spec in item.queries:
            if spec.note:
                texts.append((f"{item.id}/{spec.language}/note", spec.note))
            if spec.equivalent.note:
                texts.append((f"{item.id}/{spec.language}/equivalent.note", spec.equivalent.note))
    return texts


@pytest.mark.parametrize(("where", "text"), _shown_texts(), ids=lambda value: str(value)[:40])
def test_catalog_text_shown_to_users_ends_in_the_polite_form(where: str, text: str) -> None:
    impolite = [s for s in _sentences(text) if not s.endswith(POLITE_ENDINGS)]
    assert impolite == [], where
