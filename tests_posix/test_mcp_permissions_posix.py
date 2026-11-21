"""ネイティブ設定のパスと権限だけを確かめる。"""

import logging
import os
import sys
from pathlib import Path

import pytest
from test_security import write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_PROCESSING_FAILURE
from jigane.skills import mcp
from jigane.skills.mcp import MCP_CONFIG_DIRECTORY, McpSelection, SecurityConfigError
from jigane.storage.agent_selection import Agent, save_agent
from jigane.storage.review_error import ReviewError
from jigane.storage.store import data_home


def _registration() -> Path:
    return data_home() / MCP_CONFIG_DIRECTORY / "mobile-sec.json"


@pytest.mark.parametrize(("mode", "warning_count"), [(0o600, 0), (0o640, 1), (0o604, 1)])
def test_load_warns_when_native_config_is_broadly_accessible(
    caplog: pytest.LogCaptureFixture, mode: int, warning_count: int
) -> None:
    write_mcp(("mobile-sec",))
    _registration().chmod(mode)
    with caplog.at_level(logging.WARNING, logger="jigane.skills.mcp"):
        selection = McpSelection.load(("mobile-sec",))
    assert selection.names == ("mobile-sec",)
    assert len([message for message in caplog.messages if "権限" in message]) == warning_count


@pytest.mark.parametrize("command", ["run", "estimate"])
def test_cli_reports_how_to_fix_writable_config(
    command: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "mcp config"))
    save_agent(Agent.CLAUDE)
    write_mcp(("mobile-sec",))
    path = _registration().resolve()
    path.chmod(0o666)

    def unexpected_consent(_prompt: str) -> str:
        pytest.fail("権限の検査を通る前に同意を求めました")

    assert (
        main(
            [command, "sample", "security", "--mcps", "mobile-sec"],
            is_terminal=lambda: True,
            read_answer=unexpected_consent,
        )
        == EXIT_PROCESSING_FAILURE
    )
    error = capsys.readouterr().err
    assert "グループか他の利用者が書き込めます" in error
    assert f"chmod 600 '{path}'" in error


def test_config_directory_must_not_be_writable_by_others() -> None:
    write_mcp(("mobile-sec",))
    _registration().parent.chmod(0o777)
    with pytest.raises(SecurityConfigError, match="chmod 700"):
        McpSelection.load(("mobile-sec",))


def test_registration_home_is_checked_for_both_clis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registrations = tmp_path / "mcp registry"
    registrations.mkdir()
    registrations.chmod(0o777)
    monkeypatch.setenv("JIGANE_MCP_HOME", str(registrations))
    for codex_cli in (False, True):
        with pytest.raises(ReviewError, match="chmod 700"):
            McpSelection.load(("mobile-sec",), codex_cli=codex_cli)


@pytest.mark.parametrize("kind", ["fifo", "symlink", "directory"])
def test_config_must_be_a_regular_file(kind: str) -> None:
    write_mcp(("mobile-sec",))
    path = _registration()
    path.unlink()
    match kind:
        case "fifo":
            if sys.platform != "win32":  # noqa: TID251 Windows には mkfifo がない
                os.mkfifo(path)
        case "symlink":
            path.symlink_to(path.with_name("another.json"))
        case "directory":
            path.mkdir()
    with pytest.raises(SecurityConfigError, match="通常のファイル"):
        McpSelection.load(("mobile-sec",))


def test_unselected_mcp_does_not_look_up_config(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_lookup() -> None:
        pytest.fail("MCP を指定していないときに登録を調べました")

    monkeypatch.setattr(mcp, "data_home", unexpected_lookup)
    assert McpSelection.load(()) == McpSelection.none()
