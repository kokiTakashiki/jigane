"""非対話の単群で、保存済み Codex ログインと送信前の予算照合を確かめる。"""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_USAGE
from jigane.commands.subcommands import estimate, run
from jigane.llm_runs.usage_table import TOTAL_ROLE

REVIEW = "octo/synthetic@sample"


@pytest.mark.parametrize("group", ["macro", "micro", "security"])
@pytest.mark.parametrize("key_configured", [False, True])
def test_login_debug_matches_estimate_and_records_usage_from_a_non_tty(
    codex_trace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    group: str,
    *,
    key_configured: bool,
) -> None:
    if key_configured:
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    capsys.readouterr()
    assert main(["estimate", REVIEW, group, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    price = report["expected"]["total_usd"]
    assert report["cli_auth"] == "chatgpt_login"
    assert report["noninteractive_requires"] == "codex login"
    assert not codex_trace.with_name("login-checked").exists()
    launch = subprocess.run(  # noqa: S603, TID251  組み込み Python で非TTY の CLI を直接起動する
        [
            sys.executable,
            "-m",
            "jigane",
            "debug",
            "run",
            REVIEW,
            group,
            "--yes",
            "--max-usd",
            str(price),
            "--expect-agent",
            "codex",
            "--json",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert launch.returncode == 0, launch.stderr
    result = json.loads(launch.stdout)
    assert result["estimated_usd"] == price
    roles = codex_trace.read_text(encoding="utf-8").splitlines()
    assert (
        roles
        == {
            "macro": ["diagram"],
            "micro": ["discovery", "rebuttal", "judge", "themes"],
            "security": ["discovery", "rebuttal", "judge"],
        }[group]
    )
    if group != "macro":
        assert result["findings"][0]["adjudication"]["verdict"] == "kept"
    assert result["totals"]["invocation_count"] == len(roles)
    assert result["totals"]["usage_priced_cost_usd"] > 0
    assert all(row["cost_source"] == "usage_priced" for row in result["run"]["invocations"])
    assert all("max_budget_usd" not in row for row in result["run"]["invocations"])
    assert main(["usage", REVIEW, "--json"]) == 0
    (stored,) = (
        row for row in json.loads(capsys.readouterr().out)["rows"] if row["role"] == TOTAL_ROLE
    )
    assert stored["usage_priced_cost_usd"] == result["totals"]["usage_priced_cost_usd"]
    assert stored["output_tokens"] == 5 * len(roles)


@pytest.mark.parametrize("basis", ["input_only", "before_verification", "below_estimate"])
def test_login_debug_over_budget_checks_neither_login_nor_generation(
    codex_trace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    basis: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    capsys.readouterr()
    assert main(["estimate", REVIEW, "micro", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    expected = report["expected"]
    discovery = expected["discovery_estimate_usd"]
    price = expected["total_usd"]
    assert discovery > report["launches"][0]["input_usd"]
    assert price == pytest.approx(discovery * 2.5)
    limits = {
        "input_only": str(report["launches"][0]["input_usd"]),
        "before_verification": str(discovery),
        # float に丸めると見込みと同額になる十進の上限でも、送信前に止まる。
        "below_estimate": str(Decimal(str(price)) - Decimal("0.00000000000000000001")),
    }
    assert (
        main(["debug", "run", REVIEW, "micro", "--yes", "--max-usd", limits[basis], "--json"]) == 2
    )
    stopped = json.loads(capsys.readouterr().out)
    assert stopped["stopped"] == "estimate_exceeds_max_usd"
    assert stopped["estimated_usd"] == price
    assert not codex_trace.exists()
    assert not codex_trace.with_name("login-checked").exists()


@pytest.mark.parametrize("status", ["Not logged in", "API key configured - synthetic-mask"])
def test_login_debug_refuses_other_authentication_before_recording_a_run(
    codex_trace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    (codex_trace.with_name("codex-login") / "login-mode").write_text(status, encoding="utf-8")
    capsys.readouterr()
    assert main(["debug", "run", REVIEW, "macro", "--yes", "--max-usd", "1", "--json"]) == 2
    captured = capsys.readouterr()
    assert "`codex login`" in captured.err
    assert status not in captured.err
    assert "error" in json.loads(captured.out)
    assert codex_trace.with_name("login-checked").exists()
    assert not codex_trace.exists()
    assert main(["show", REVIEW, "macro", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["run"] is None


def test_login_debug_refuses_unavailable_estimate(
    codex_trace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(estimate, "launch_prompts", lambda _: ())
    monkeypatch.setattr(run, "launch_prompts", lambda _: ())
    capsys.readouterr()
    assert main(["estimate", REVIEW, "macro", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["expected"]["total_usd"] is None
    assert report["noninteractive_run_available"] is False
    assert main(["debug", "run", REVIEW, "macro", "--yes", "--max-usd", "1"]) == 2
    assert "費用の見込みがない" in capsys.readouterr().err
    assert not codex_trace.exists()
    assert not codex_trace.with_name("login-checked").exists()


def test_login_debug_refuses_upper_limit_over_one_dollar_before_login(
    codex_trace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    capsys.readouterr()
    args = ["debug", "run", REVIEW, "macro", "--yes", "--max-usd", "1.0000000000000000001"]
    assert main([*args, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["stopped"] == "confirmation_required"
    assert not codex_trace.exists()
    assert not codex_trace.with_name("login-checked").exists()


@pytest.mark.parametrize(
    ("arguments", "rejected", "exit_code"),
    [
        (["all"], "all", EXIT_USAGE),
        (["macro", "--budget-id", "synthetic"], "--budget-id", 2),
        (["macro", "--expect-range", "a..b"], "--expect-range", 2),
    ],
)
def test_debug_run_rejects_removed_arguments_before_login(
    codex_trace: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    rejected: str,
    exit_code: int,
) -> None:
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        main(["debug", "run", REVIEW, *arguments, "--yes", "--max-usd", "1"])
    assert error.value.code == exit_code
    assert rejected in capsys.readouterr().err
    assert not codex_trace.exists()
    assert not codex_trace.with_name("login-checked").exists()


@pytest.mark.parametrize("command", [["run"], ["debug", "run"], ["estimate"]])
def test_codex_review_help_advertises_only_the_login_route(
    capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    with pytest.raises(SystemExit) as error:
        main([*command, "--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "ChatGPT ログイン" in help_text
    assert all(
        obsolete not in help_text
        for obsolete in (
            "OPENAI_API_KEY",
            "Responses API",
            "{macro,micro,security,all}",
            "--budget-id",
            "--expect-range",
        )
    )
