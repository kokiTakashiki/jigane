"""Windows だけで意味を持つ、外部コマンドの起動の口の試験。

既定の testpaths の外に置き、Windows の CI の job だけが明示して走らせる。
偽の claude.cmd と claude.exe を一時のディレクトリに置き、PATH をそこだけに向けて確かめる。
"""

import os
import subprocess
from pathlib import Path

import pytest

from jigane.git.process import CommandError, resolve_command, run_command

JSON_ARGUMENT = '{"name": "value"}'
PLAIN_ARGUMENT = "plain-argument"


def isolated_environment(directory: Path) -> dict[str, str]:
    # cmd.exe の起動に SystemRoot などが要るので、PATH だけを差し替える
    return {**os.environ, "PATH": str(directory)}


@pytest.fixture
def fake_cmd_directory(tmp_path: Path) -> Path:
    (tmp_path / "claude.cmd").write_bytes(b"@echo off\r\necho %*\r\n")
    return tmp_path


def test_cmd_script_rejects_json_argument(fake_cmd_directory: Path) -> None:
    environment = isolated_environment(fake_cmd_directory)

    with pytest.raises(CommandError) as raised:
        resolve_command(["claude", JSON_ARGUMENT], environment)

    assert not raised.value.missing_executable
    assert "cmd.exe" in str(raised.value)


def test_cmd_script_resolves_and_launches_with_plain_argument(fake_cmd_directory: Path) -> None:
    environment = isolated_environment(fake_cmd_directory)

    resolved = resolve_command(["claude", PLAIN_ARGUMENT], environment)

    assert os.path.normcase(resolved[0]) == os.path.normcase(
        str(fake_cmd_directory / "claude.cmd")
    )
    assert resolved[1:] == [PLAIN_ARGUMENT]
    output = run_command(["claude", PLAIN_ARGUMENT], env=environment)
    assert PLAIN_ARGUMENT in output


def test_exe_is_preferred_over_cmd(tmp_path: Path) -> None:
    # 中身の無いファイルでよい。起動はせず、which が選ぶ順だけを確かめる
    (tmp_path / "claude.exe").write_bytes(b"")
    (tmp_path / "claude.cmd").write_bytes(b"@echo off\r\n")
    environment = isolated_environment(tmp_path)

    resolved = resolve_command(["claude", JSON_ARGUMENT], environment)

    assert os.path.normcase(resolved[0]) == os.path.normcase(str(tmp_path / "claude.exe"))
    assert resolved[1:] == [JSON_ARGUMENT]


def test_executable_in_the_current_directory_is_not_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "git.exe").write_bytes(b"")
    path_directory = tmp_path / "bin"
    path_directory.mkdir()
    (path_directory / "claude.cmd").write_bytes(b"@echo off\r\n")
    monkeypatch.chdir(work)
    environment = isolated_environment(path_directory)

    with pytest.raises(CommandError) as raised:
        resolve_command(["git", "--version"], environment)

    assert raised.value.missing_executable


def test_exe_resolved_path_is_what_reaches_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "claude.exe").write_bytes(b"")
    (tmp_path / "claude.cmd").write_bytes(b"@echo off\r\n")
    environment = isolated_environment(tmp_path)
    recorded: list[list[str]] = []

    def record_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", record_run)

    run_command(["claude", JSON_ARGUMENT], env=environment)

    assert len(recorded) == 1
    assert os.path.normcase(recorded[0][0]) == os.path.normcase(str(tmp_path / "claude.exe"))
    assert Path(recorded[0][0]).is_absolute()
    assert recorded[0][1:] == [JSON_ARGUMENT]
