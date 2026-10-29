"""claude の起動が jigane.llm_runs の口以外から断られることの試験。"""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jigane.git import process
from jigane.git.process import (
    CommandError,
    run_claude_resolved,
    run_command,
    run_resolved,
)

# 列の先頭に "claude" の文字列を書くと ast-grep の規則が落とす。試験では名前を経由する
CLAUDE = "claude"


class Launched:
    """subprocess.run の代わり。起動の要求を記録するだけで、何も起動しない。"""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        # claude の版の確かめ（--version）への答え
        self.version = ""

    def run(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[1:] == ["--version"] and self.version:
            return subprocess.CompletedProcess(command, 0, self.version, "")
        return subprocess.CompletedProcess(command, 0, "ok", "")


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> Launched:
    recorder = Launched()
    monkeypatch.setattr(subprocess, "run", recorder.run)
    monkeypatch.delenv("JIGANE_CLAUDE_BINARY", raising=False)
    return recorder


@pytest.mark.parametrize(
    "executable",
    [CLAUDE, "CLAUDE", "Claude.EXE", "/usr/local/bin/claude", r"C:\bin\Claude.cmd"],
)
def test_run_command_refuses_claude_by_any_spelling(launched: Launched, executable: str) -> None:
    with pytest.raises(CommandError) as raised:
        run_command([executable, "-p"])
    assert "jigane.llm_runs から起動する" in raised.value.stderr
    assert launched.commands == []


@pytest.mark.parametrize("launcher", [run_resolved, run_command])
def test_launchers_refuse_the_replaced_binary(
    launched: Launched,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launcher: Callable[..., Any],
) -> None:
    fake = tmp_path / "fake-agent"
    monkeypatch.setenv("JIGANE_CLAUDE_BINARY", str(fake))
    with pytest.raises(CommandError, match=r"jigane\.llm_runs から起動する"):
        launcher([str(fake), "-p"])
    assert launched.commands == []


def test_replaced_binary_is_refused_by_its_bare_name(
    launched: Launched, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIGANE_CLAUDE_BINARY", "/somewhere/Fake.sh")
    with pytest.raises(CommandError):
        run_command(["fake", "-p"])
    assert launched.commands == []


def test_listed_commands_launch(launched: Launched) -> None:
    assert run_command(["git", "--version"]) == "ok"
    assert run_command(["gh", "--version"]) == "ok"
    assert launched.commands == [["git", "--version"], ["gh", "--version"]]


@pytest.mark.parametrize(
    "argv",
    [
        ["sh", "-c", f"{CLAUDE} -p x"],
        ["bash", "-c", f"{CLAUDE} -p x"],
        ["env", CLAUDE, "-p"],
        ["cmd", "/c", CLAUDE, "-p"],
        ["powershell", "-Command", f"{CLAUDE} -p"],
        ["python", "-c", "pass"],
        ["SH", "-c", "true"],
        ["/bin/bash", "-c", "true"],
        [r"C:\Windows\System32\cmd.exe", "/c", "dir"],
        ["PowerShell.EXE", "-Command", "ls"],
        ["git-shell"],
    ],
)
@pytest.mark.parametrize("launcher", [run_resolved, run_command])
def test_launchers_refuse_executables_outside_the_list(
    launched: Launched, launcher: Callable[..., Any], argv: list[str]
) -> None:
    with pytest.raises(CommandError, match="一覧") as raised:
        launcher(argv)
    assert "に無い" in raised.value.stderr
    assert launched.commands == []


@pytest.mark.parametrize("executable", ["GIT", "Git.exe", "/usr/bin/git", r"C:\bin\GH.EXE"])
def test_listed_names_match_by_any_spelling(launched: Launched, executable: str) -> None:
    assert run_command([executable, "--version"]) == "ok"
    assert launched.commands == [[executable, "--version"]]


def test_llm_runs_entrance_launches_claude(
    launched: Launched, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (CLAUDE, f"{CLAUDE}.exe"):
        (tmp_path / name).write_text("", encoding="utf-8")
        (tmp_path / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(process, "_checked_claude_binaries", set())
    launched.version = "2.1.292 (Claude Code)"
    result = run_claude_resolved([CLAUDE, "-p"])
    assert result.stdout == "ok"
    # 版を確かめた実体と同じ絶対パスで起動する
    (version, launch) = launched.commands
    assert Path(version[0]).parent == tmp_path
    assert version[1:] == ["--version"]
    assert launch == [version[0], "-p"]
