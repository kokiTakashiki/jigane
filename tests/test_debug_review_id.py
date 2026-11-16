import json
import re
from typing import Any

import pytest

from jigane import commands
from jigane.storage.store import ReviewId, ReviewKind


def run_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert commands.main(["debug", "review-id", "--json"]) == 0
    body: dict[str, Any] = json.loads(capsys.readouterr().out)
    return body


def test_json_has_fixed_keys_and_types(capsys: pytest.CaptureFixture[str]) -> None:
    body = run_json(capsys)
    assert list(body) == ["pattern", "kinds"]
    assert isinstance(body["pattern"], str)
    for item in body["kinds"]:
        assert list(item) == ["kind", "form"]
        assert isinstance(item["kind"], str)
        assert isinstance(item["form"], str)


def test_kinds_match_every_review_kind(capsys: pytest.CaptureFixture[str]) -> None:
    kinds = run_json(capsys)["kinds"]
    assert [item["kind"] for item in kinds] == [kind.value for kind in ReviewKind]
    for item, kind in zip(kinds, ReviewKind, strict=True):
        assert item["form"] == f"owner/repo{kind.separator}<キー>"


def test_pattern_matches_valid_identifiers_like_parse(capsys: pytest.CaptureFixture[str]) -> None:
    pattern = re.compile(run_json(capsys)["pattern"])
    keys = {ReviewKind.PULL_REQUEST: "123"}
    for kind in ReviewKind:
        text = f"acme/tool{kind.separator}{keys.get(kind, 'name-1')}"
        assert pattern.match(text)
        assert str(ReviewId.parse(text)) == text


@pytest.mark.parametrize(
    "text", ["acme/tool", "acme#1", "acme/tool#", "acme/tool 1", "/tool#1", "a/b/c#1", ""]
)
def test_pattern_rejects_broken_identifiers_like_parse(
    capsys: pytest.CaptureFixture[str], text: str
) -> None:
    pattern = re.compile(run_json(capsys)["pattern"])
    assert pattern.match(text) is None
    with pytest.raises(ValueError, match="識別子"):
        ReviewId.parse(text)


def test_text_output_lists_kinds_and_states_provisional(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert commands.main(["debug", "review-id"]) == 0
    out = capsys.readouterr().out
    assert "pull_request" in out
    assert "暫定" in out
