"""claude を起動する前に版を確かめる試験。subprocess.run を差し替え、何も起動しない。"""

import stat
import subprocess
from pathlib import Path

import pytest
from test_claude import make_invocation

from jigane.commands import failures
from jigane.git import process
from jigane.git.process import CommandError
from jigane.llm_runs import claude

# 列の先頭に "claude" の文字列を書くと ast-grep の規則が落とす。試験では名前を経由する
NAME = "claude"
SUCCESS = '{"is_error": false, "structured_output": {}}'


class FakeClaude:
    """subprocess.run の代わり。--version には決めた出力を返し、起動の要求を記録する。"""

    def __init__(self, version_output: str, version_returncode: int = 0) -> None:
        self.version_output = version_output
        self.version_returncode = version_returncode
        self.commands: list[list[str]] = []

    def run(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(
                command, self.version_returncode, self.version_output, ""
            )
        return subprocess.CompletedProcess(command, 0, SUCCESS, "")


@pytest.fixture
def directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # PATH で見つかる実体を置く。Windows は拡張子つきの名前を探す
    for name in (NAME, f"{NAME}.exe"):
        binary = tmp_path / name
        binary.write_text("", encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(process, "_checked_claude_binaries", set())
    monkeypatch.delenv("JIGANE_CLAUDE_BINARY", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    return tmp_path


def _launch(directory: Path, fake: FakeClaude, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", fake.run)
    claude.run(make_invocation(directory))


@pytest.mark.parametrize("output", ["2.1.257 (Claude Code)\n", "2.1.292 (Claude Code)\n"])
def test_supported_version_launches_the_same_resolved_binary(
    directory: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    fake = FakeClaude(output)
    _launch(directory, fake, monkeypatch)
    _launch(directory, fake, monkeypatch)
    version, first, second = fake.commands
    # 確かめと起動は、PATH を一度引いて解いた同じ絶対パスで行い、確かめは一度だけ
    assert Path(version[0]).is_absolute()
    assert Path(version[0]).parent == directory
    assert version == [version[0], "--version"]
    assert first[0] == second[0] == version[0]
    assert "-p" in first


@pytest.mark.parametrize(
    ("output", "returncode", "expected"),
    [
        ("2.1.256 (Claude Code)\n", 0, "Claude Code 2.1.256 は"),
        ("Claude Code\n", 0, "版を読み取れない"),
        ("2.1.292 (Claude Code)\n", 1, "版を読み取れない"),
    ],
)
def test_old_or_unreadable_version_stops_before_launch(
    directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    returncode: int,
    expected: str,
) -> None:
    fake = FakeClaude(output, returncode)
    with pytest.raises(CommandError) as raised:
        _launch(directory, fake, monkeypatch)
    assert expected in raised.value.stderr
    assert "2.1.257 以降" in raised.value.stderr
    assert [command[1:] for command in fake.commands] == [["--version"]]
    # 起動の失敗と同じ分類で利用者に示す
    launch_failure = CommandError([NAME, "-p"], "起動できませんでした")
    assert failures.describe(raised.value).kind == failures.describe(launch_failure).kind
