import json

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_PROCESSING_FAILURE


def test_sequence_format_json_prints_the_failure_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["sequence", "local/shop:none", "--format", "json"]) == EXIT_PROCESSING_FAILURE
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["kind"] == "review"
    assert captured.err.startswith("jigane: ")


def test_sequence_format_mermaid_prints_no_failure_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["sequence", "local/shop:none", "--format", "mermaid"]) == EXIT_PROCESSING_FAILURE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("jigane: ")
