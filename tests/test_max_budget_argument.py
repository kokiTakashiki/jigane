from pathlib import Path

import pytest
from fakes import MicroFake

from jigane.commands import main
from jigane.commands.budget_argument import positive_finite_usd
from jigane.reviews.lifecycle import create_feature_review


@pytest.fixture
def review_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    return str(create_feature_review(root, ["src"], name="budgetarg").id)


def _forbidden(prompt: str) -> str:
    raise AssertionError(prompt)


@pytest.mark.parametrize("command", ["run", "estimate"])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "abc"])
def test_invalid_max_budget_is_argparse_error_without_launch(
    review_name: str, command: str, value: str
) -> None:
    fake = MicroFake({1: []})
    with pytest.raises(SystemExit) as exited:
        main(
            [command, review_name, "micro", f"--max-budget-usd-per-call={value}"],
            invoke=fake,
            is_terminal=lambda: False,
            read_answer=_forbidden,
        )
    assert exited.value.code == 2
    assert fake.calls == []


@pytest.mark.parametrize(("text", "expected"), [("0.5", 0.5), ("1e-3", 0.001), ("2", 2.0)])
def test_positive_finite_usd_accepts_finite_positive_values(text: str, expected: float) -> None:
    assert positive_finite_usd(text) == expected
