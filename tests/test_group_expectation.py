import json
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import create_feature_review
from jigane.skills.group_expectation import (
    DEFAULT_VERIFIER_MULTIPLIER,
    EXPECTATION_VERSION,
    NO_VERIFIER_MULTIPLIER,
    GroupExpectation,
    VerifierMultiplierSource,
)


@pytest.fixture
def review_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    return str(create_feature_review(root, ["src"], name="expectation").id)


def _expected(review: str, group: str, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    assert main(["estimate", review, group, "--json"]) == EXIT_OK
    expected = json.loads(capsys.readouterr().out)["expected"]
    assert isinstance(expected, dict)
    return expected


@pytest.mark.parametrize("group", ["macro", "micro", "security"])
def test_estimate_json_carries_the_group_expectation(
    review_name: str, group: str, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = _expected(review_name, group, capsys)

    assert expected["version"] == EXPECTATION_VERSION
    assert expected["total_usd"] == pytest.approx(
        float(expected["discovery_estimate_usd"]) * float(expected["verifier_multiplier"])  # type: ignore[arg-type]
    )


def test_macro_has_no_verifier_multiplier(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = _expected(review_name, "macro", capsys)

    assert expected["verifier_multiplier"] == NO_VERIFIER_MULTIPLIER
    assert expected["verifier_multiplier_source"] == "no_verifier"


@pytest.mark.parametrize("group", ["micro", "security"])
def test_default_multiplier_without_round_records(
    review_name: str, group: str, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = _expected(review_name, group, capsys)

    assert expected["verifier_multiplier"] == DEFAULT_VERIFIER_MULTIPLIER
    assert expected["verifier_multiplier_source"] == "default"


def test_group_expectation_is_the_product() -> None:
    result = GroupExpectation(0.3, 2.0, VerifierMultiplierSource.RUN_RECORDS)

    assert result.source is VerifierMultiplierSource.RUN_RECORDS
    assert result.total_usd == pytest.approx(0.6)
