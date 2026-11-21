import json
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.storage import atomic_write
from jigane.storage.agent_selection import AGENT_FILE
from jigane.storage.store import data_home


def test_select_agent_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "unselected"
    monkeypatch.setenv("JIGANE_HOME", str(home))
    assert main(["select-agent", "--show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"agent": None}
    assert not home.exists()

    assert main(["select-agent", "claude"]) == 0
    capsys.readouterr()
    assert main(["select-agent"], is_terminal=lambda: True, read_answer=lambda _: "codex") == 0
    assert main(["select-agent"], is_terminal=lambda: True, read_answer=lambda _: "") == 2

    def interrupt(_: str) -> str:
        raise KeyboardInterrupt

    assert main(["select-agent"], is_terminal=lambda: True, read_answer=interrupt) == 130
    capsys.readouterr()
    assert main(["select-agent", "--show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"agent": "codex"}


@pytest.mark.parametrize("command", [["run"], ["debug", "run", "--yes", "--max-usd", "1"]])
def test_run_requires_saved_agent(
    command: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JIGANE_AGENT", "codex")
    assert main([*command, "missing", "macro", "--json"]) == 2
    captured = capsys.readouterr()
    assert "jigane select-agent" in captured.err
    assert json.loads(captured.out)["error"]["kind"] == "agent_selection"


def test_corrupt_selection_can_be_replaced(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["select-agent", "claude"]) == 0
    capsys.readouterr()
    path = data_home() / AGENT_FILE
    path.write_text('{"agent":"unknown"}', encoding="utf-8")
    assert main(["select-agent", "--show", "--json"]) == 2
    assert "agent.json" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == '{"agent":"unknown"}'
    assert main(["select-agent", "claude"]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {"agent": "claude"}


def test_debug_run_rejects_an_agent_change_after_consent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["select-agent", "claude"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "debug",
                "run",
                "--yes",
                "--max-usd",
                "0.7",
                "--expect-agent",
                "codex",
                "missing",
                "macro",
                "--json",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["kind"] == "agent_selection"
    assert "選択が変わりました" in captured.err
    assert "missing" not in captured.err


def test_failed_save_keeps_previous_selection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["select-agent", "claude"]) == 0
    capsys.readouterr()

    def refuse_replace(_temporary: Path, _target: Path) -> None:
        raise PermissionError("synthetic replacement failure")

    monkeypatch.setattr(atomic_write, "_replace_retrying", refuse_replace)
    assert main(["select-agent", "codex"]) == 2
    assert main(["select-agent", "--show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"agent": "claude"}
