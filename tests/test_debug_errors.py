import json
from typing import Any

import pytest

from jigane import commands
from jigane.commands import failures


def run_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert commands.main(["debug", "errors", "--json"]) == 0
    body: dict[str, Any] = json.loads(capsys.readouterr().out)
    return body


def test_json_has_fixed_keys_and_types(capsys: pytest.CaptureFixture[str]) -> None:
    body = run_json(capsys)
    assert list(body) == ["fields", "kinds"]
    assert body["fields"] == ["kind", "title", "reason", "action"]
    kinds = body["kinds"]
    assert isinstance(kinds, list)
    assert kinds
    for item in kinds:
        assert list(item) == ["kind", "description", "extra_fields"]
        assert isinstance(item["kind"], str)
        assert isinstance(item["description"], str)
        assert item["description"]
        assert isinstance(item["extra_fields"], list)


def test_kinds_match_every_kind_in_failures(capsys: pytest.CaptureFixture[str]) -> None:
    body = run_json(capsys)
    listed = [item["kind"] for item in body["kinds"]]
    expected = {
        *(kind.kind for _, kind in failures.KINDS),
        *(kind.kind for kind in failures.COMMAND_KINDS.values()),
        failures.MISSING_TOOL_KIND.kind,
        failures.UNEXPECTED,
    }
    assert set(listed) == expected
    assert len(listed) == len(set(listed))


def test_text_output_lists_kinds_and_states_provisional(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert commands.main(["debug", "errors"]) == 0
    out = capsys.readouterr().out
    assert "unexpected" in out
    assert "暫定" in out


def test_review_exists_adds_review_and_others_add_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    extras = {item["kind"]: item["extra_fields"] for item in run_json(capsys)["kinds"]}
    assert extras[failures.REVIEW_EXISTS_KIND] == ["review"]
    assert [kind for kind, extra in extras.items() if extra] == [failures.REVIEW_EXISTS_KIND]
