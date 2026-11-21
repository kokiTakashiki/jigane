import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import MicroFake

from jigane.commands import main
from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_ESTIMATE_OVER_LIMIT, EXIT_OK, EXIT_USAGE
from jigane.commands.subcommands import debug
from jigane.commands.subcommands import run as run_subcommand
from jigane.reviews.lifecycle import create_feature_review

pytestmark = pytest.mark.usefixtures("selected_agent")


@pytest.fixture
def review_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    return str(create_feature_review(root, ["src"], name="debugrun").id)


def _forbidden(prompt: str) -> str:
    raise AssertionError(prompt)


def _debug_run(review: str, *options: str, fake: MicroFake) -> int:
    return main(
        ["debug", "run", review, "micro", *options],
        invoke=fake,
        is_terminal=lambda: False,
        read_answer=_forbidden,
    )


def test_yes_without_max_usd_is_usage_error(review_name: str) -> None:
    fake = MicroFake({1: []})
    assert _debug_run(review_name, "--yes", fake=fake) == EXIT_USAGE
    assert fake.calls == []


def test_without_yes_is_usage_error(review_name: str) -> None:
    fake = MicroFake({1: []})
    assert _debug_run(review_name, "--max-usd", "100", fake=fake) == EXIT_USAGE
    assert _debug_run(review_name, fake=fake) == EXIT_USAGE
    assert fake.calls == []


def test_estimate_above_max_usd_does_not_launch(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = MicroFake({1: []})
    code = _debug_run(review_name, "--yes", "--max-usd", "0.0001", "--json", fake=fake)
    assert code == EXIT_ESTIMATE_OVER_LIMIT
    assert fake.calls == []
    body: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert body.keys() >= debug.DEBUG_RUN_JSON_KEYS
    assert body["experimental"] is True
    assert body["max_usd"] == 0.0001
    assert body["estimated_usd"] > 0.0001
    assert body["stopped"] == "estimate_exceeds_max_usd"


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "abc"])
def test_invalid_max_usd_is_usage_error(review_name: str, value: str) -> None:
    fake = MicroFake({1: []})
    with pytest.raises(SystemExit) as exited:
        _debug_run(review_name, "--yes", f"--max-usd={value}", fake=fake)
    assert exited.value.code == EXIT_USAGE
    assert fake.calls == []


def test_within_max_usd_launches_and_json_has_the_result_keys(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = MicroFake({1: []})
    code = _debug_run(review_name, "--yes", "--max-usd", "100", "--json", fake=fake)
    assert code == EXIT_OK
    assert fake.calls != []
    body: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert body["experimental"] is True
    assert body["max_usd"] == 100
    assert body["estimated_usd"] > 0
    assert {"findings", "run", "totals", "failures"} <= set(body)


def test_help_states_experimental_and_human_consent(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["debug", "run", "--help"])
    out = capsys.readouterr().out
    assert "実験的" in out
    assert "同意" in out
    assert "暫定" in out


def test_added_keys_do_not_clash_with_run_json_keys(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert {"max_usd", "estimated_usd", "experimental"} == debug.DEBUG_RUN_JSON_KEYS
    assert not debug.DEBUG_RUN_JSON_KEYS & run_subcommand.RUN_JSON_KEYS
    assert not debug.DebugRunAdded.__annotations__.keys() & run_subcommand.RUN_JSON_KEYS
    code = main(
        ["run", review_name, "micro", "--json"],
        invoke=MicroFake({1: []}),
        is_terminal=lambda: True,
        read_answer=lambda _prompt: "y",
    )
    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out).keys() == run_subcommand.RUN_JSON_KEYS


def test_debug_run_json_is_run_json_plus_added_keys(
    review_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _debug_run(review_name, "--yes", "--max-usd", "100", "--json", fake=MicroFake({1: []}))
    assert code == EXIT_OK
    keys = json.loads(capsys.readouterr().out).keys()
    assert keys == run_subcommand.RUN_JSON_KEYS | debug.DEBUG_RUN_JSON_KEYS


def test_nan_estimate_is_treated_as_over_limit_and_does_not_launch(
    review_name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_prepare = run_subcommand.prepare_run

    def prepare_with_nan(args: argparse.Namespace, context: Context) -> SimpleNamespace:
        prepared = real_prepare(args, context)
        plan = SimpleNamespace(expectation=SimpleNamespace(total_usd=math.nan))
        return SimpleNamespace(plan=plan, assembly=prepared.assembly, settings=prepared.settings)

    monkeypatch.setattr(run_subcommand, "prepare_run", prepare_with_nan)
    fake = MicroFake({1: []})
    code = _debug_run(review_name, "--yes", "--max-usd", "100", "--json", fake=fake)
    assert code == EXIT_ESTIMATE_OVER_LIMIT
    assert fake.calls == []
    assert json.loads(capsys.readouterr().out)["stopped"] == "estimate_exceeds_max_usd"


def test_clashing_json_keys_do_not_launch(
    review_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clashing = frozenset(run_subcommand.RUN_JSON_KEYS | debug.DebugRunAdded.__annotations__.keys())
    monkeypatch.setattr(run_subcommand, "RUN_JSON_KEYS", clashing)
    fake = MicroFake({1: []})
    code = _debug_run(review_name, "--yes", "--max-usd", "100", "--json", fake=fake)
    assert code == EXIT_USAGE
    assert fake.calls == []
