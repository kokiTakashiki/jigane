"""PATH から git を探す規則のうち、POSIX の実ファイルと実行ビットに頼る試験。"""

import os
import subprocess
from pathlib import Path

import pytest
from test_process_windows import Launches

from jigane.git import process
from jigane.git.process import CommandError, run_command


def _fake_executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\necho fake\n")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("relative", [".", "bin", ""])
def test_relative_path_entries_are_not_searched_for_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    _fake_executable(tmp_path / "bin", "git")
    _fake_executable(tmp_path, "git")
    trusted = tmp_path / "trusted"
    expected = _fake_executable(trusted, "git")
    monkeypatch.chdir(tmp_path)
    path = os.pathsep.join([relative, str(trusted)])
    assert process.resolve_command(["git", "status"], {"PATH": path}) == [str(expected), "status"]


def test_git_is_launched_by_absolute_path_from_absolute_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _fake_executable(tmp_path / "trusted", "git")
    launched: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        launched.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    process.run_resolved(["git", "--version"], env={"PATH": str(tmp_path / "trusted")})
    assert launched == [[str(expected), "--version"]]


def test_git_in_only_relative_path_entry_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_executable(tmp_path / "bin", "git")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(CommandError) as raised:
        process.resolve_command(["git"], {"PATH": "bin"})
    assert raised.value.missing_executable


def test_posix_launches_the_absolute_path_from_the_path_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launches = Launches()
    fake = tmp_path / "git"
    fake.write_text("")
    fake.chmod(0o755)
    monkeypatch.setattr(process, "is_windows", lambda: False)
    monkeypatch.setattr(subprocess, "run", launches.run)
    monkeypatch.setenv("PATH", str(tmp_path))
    run_command(["git", "status"])
    assert launches.commands == [[str(fake), "status"]]
