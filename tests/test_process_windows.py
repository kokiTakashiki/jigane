import ntpath
import shutil
import subprocess
from pathlib import Path

import pytest
from dev_launch import run_tool
from markdown_support import section_prose
from test_claude import make_invocation

from jigane.commands.failures import describe
from jigane.git import process
from jigane.git.process import CommandError, run_claude_resolved, run_command
from jigane.llm_runs.claude import ClaudeInvocationError, run

CLAUDE_CMD = r"C:\Users\me\AppData\Roaming\npm\claude.cmd"
NPM_TOOL_CMD = r"C:\Users\me\AppData\Roaming\npm\npm-tool.cmd"


class Launches:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.raises: OSError | None = None
        self.which_names: list[str] = []

    def run(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(command, 0, "{}", "")


def windows(monkeypatch: pytest.MonkeyPatch, installed: dict[str, str]) -> Launches:
    """installed は、コマンドの名前から、which が返す絶対パスへの辞書。

    PATH は installed の置き場だけにする。偽の which は、ディレクトリ付きの名前が
    絶対パスから拡張子を除いたものか、絶対パスそのものに一致するときだけ答える。
    """
    launches = Launches()
    monkeypatch.setattr(process, "is_windows", lambda: True)
    monkeypatch.setenv("PATH", ";".join(ntpath.dirname(found) for found in installed.values()))

    def which(name: str, path: str | None = None) -> str | None:  # noqa: ARG001 which の口に合わせる
        launches.which_names.append(name)
        for found in installed.values():
            if name.lower() in (found.lower(), ntpath.splitext(found)[0].lower()):
                return found
        return None

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", launches.run)
    return launches


def test_command_resolves_to_the_cmd_shim_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    windows(monkeypatch, {"npm-tool": NPM_TOOL_CMD})
    # 起動の一覧に無い偽の名前で解決だけを試すため、起動の口を通さず resolve_command を呼ぶ
    assert process.resolve_command(["npm-tool", "--version"], None) == [NPM_TOOL_CMD, "--version"]


def test_relative_path_entries_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    launches = windows(monkeypatch, {"git": r"C:\bin\git.exe"})
    monkeypatch.setenv("PATH", r".;;bin;C:\bin")
    run_command(["git", "status"])
    assert launches.which_names == [r"C:\bin\git"]
    assert launches.commands == [[r"C:\bin\git.exe", "status"]]


def test_file_in_the_current_directory_is_never_chosen(monkeypatch: pytest.MonkeyPatch) -> None:
    launches = windows(monkeypatch, {"git": r".\git.exe"})
    monkeypatch.setenv("PATH", r".;C:\bin")
    with pytest.raises(CommandError) as raised:
        run_command(["git", "status"])
    assert raised.value.missing_executable is True
    assert launches.commands == []
    assert all(ntpath.isabs(name) for name in launches.which_names)


def test_command_with_a_directory_is_searched_only_there(monkeypatch: pytest.MonkeyPatch) -> None:
    launches = windows(monkeypatch, {"tool": r"D:\tools\tool.exe"})
    monkeypatch.setenv("PATH", r"C:\bin")
    assert process.resolve_command([r"D:\tools\tool", "x"], None) == [r"D:\tools\tool.exe", "x"]
    assert launches.which_names == [r"D:\tools\tool"]


def test_environment_without_path_falls_back_to_the_process_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches = windows(monkeypatch, {"git": r"C:\bin\git.exe"})
    monkeypatch.setenv("PATH", r"C:\bin")
    assert process.resolve_command(["git", "x"], {"OTHER": "1"}) == [r"C:\bin\git.exe", "x"]
    assert launches.which_names == [r"C:\bin\git"]


def test_cmd_script_path_with_special_characters_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows(monkeypatch, {"tool": r"C:\a&b\tool.cmd"})
    with pytest.raises(CommandError, match=r"cmd\.exe"):
        process.resolve_command(["tool", "plain"], None)


def test_missing_command_stops_before_launching(monkeypatch: pytest.MonkeyPatch) -> None:
    launches = windows(monkeypatch, {})
    with pytest.raises(CommandError) as raised:
        run_command(["gh", "pr", "view"])
    assert launches.commands == []
    assert raised.value.missing_executable is True
    assert raised.value.stderr == "実行ファイルが見つかりません: gh"
    failure = describe(raised.value)
    assert "gh をインストールして PATH に通し" in failure.action


def test_launch_failure_without_filename_names_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches = windows(monkeypatch, {"git": r"C:\Program Files\Git\cmd\git.exe"})
    launches.raises = FileNotFoundError(2, "The system cannot find the file specified")
    with pytest.raises(CommandError) as raised:
        run_command(["git", "status"])
    assert raised.value.command == ["git", "status"]
    assert "コマンドを起動できませんでした: git" in raised.value.stderr
    assert raised.value.missing_executable is False


def test_missing_working_directory_is_not_blamed_on_the_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launches = windows(monkeypatch, {"git": r"C:\git.exe"})
    launches.raises = FileNotFoundError(2, "The system cannot find the file specified")
    with pytest.raises(FileNotFoundError):
        run_command(["git", "status"], cwd=tmp_path / "nowhere")


@pytest.mark.parametrize("special", [*'&|<>^%!"', "\n"])
def test_cmd_script_refuses_arguments_with_special_characters(
    monkeypatch: pytest.MonkeyPatch, special: str
) -> None:
    windows(monkeypatch, {"npm-tool": NPM_TOOL_CMD})
    with pytest.raises(CommandError, match=r"cmd\.exe"):
        process.resolve_command(["npm-tool", f"a{special}b"], None)


def test_bat_script_refuses_too(monkeypatch: pytest.MonkeyPatch) -> None:
    windows(monkeypatch, {"tool": r"C:\bin\tool.BAT"})
    with pytest.raises(CommandError, match=r"cmd\.exe"):
        process.resolve_command(["tool", "a&b"], None)


def test_exe_accepts_special_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    windows(monkeypatch, {"tool": r"C:\bin\tool.exe"})
    assert process.resolve_command(["tool", "a&b"], None) == [r"C:\bin\tool.exe", "a&b"]


def test_claude_resolves_to_exe_and_launches_by_absolute_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = r"C:\Users\me\.local\bin\claude.exe"
    launches = windows(monkeypatch, {"claude": exe})
    # 空の出力は後段で失敗する。ここで見るのは起動のされ方だけ
    with pytest.raises(ClaudeInvocationError):
        run(make_invocation(tmp_path))
    assert launches.commands[0][0] == exe


def test_claude_cmd_is_refused_because_its_json_arguments_contain_quotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launches = windows(monkeypatch, {"claude": CLAUDE_CMD})
    with pytest.raises(CommandError, match=r"cmd\.exe"):
        run(make_invocation(tmp_path))
    assert launches.commands == []


def test_claude_cmd_refusal_gives_the_reason_and_the_official_installer_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows(monkeypatch, {"claude": CLAUDE_CMD})
    with pytest.raises(CommandError) as raised:
        run_claude_resolved(["claude", "--json-schema", '{"a": 1}'])
    message = str(raised.value)
    assert "cmd.exe を通って起動される" in message
    assert "claude.exe を入れ直してください" in message
    assert process.CLAUDE_NATIVE_INSTALL_URL in message


def test_readme_gives_the_same_installer_url_as_the_refusal() -> None:
    readme = Path(__file__).parent.parent / "README.md"
    assert process.CLAUDE_NATIVE_INSTALL_URL in section_prose(
        readme.read_text(encoding="utf-8"), "## 導入"
    )


def test_other_cmd_refusal_keeps_the_exe_advice_without_the_claude_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows(monkeypatch, {"npm-tool": NPM_TOOL_CMD})
    with pytest.raises(CommandError) as raised:
        process.resolve_command(["npm-tool", "a&b"], None)
    message = str(raised.value)
    assert ".exe の実行ファイルを PATH の先に置いてください" in message
    assert process.CLAUDE_NATIVE_INSTALL_URL not in message


def test_claude_missing_stops_before_launching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launches = windows(monkeypatch, {})
    with pytest.raises(CommandError) as raised:
        run(make_invocation(tmp_path))
    assert launches.commands == []
    assert raised.value.missing_executable is True
    assert raised.value.stderr == "実行ファイルが見つかりません: claude"
    assert "Claude Code をインストール" in describe(raised.value).action


def test_claude_launch_failure_without_filename_names_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launches = windows(monkeypatch, {"claude": r"C:\bin\claude.exe"})
    launches.raises = FileNotFoundError(2, "The system cannot find the file specified")
    with pytest.raises(CommandError) as raised:
        run(make_invocation(tmp_path))
    assert "コマンドを起動できませんでした: claude" in raised.value.stderr


def test_posix_launches_the_name_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    launches = Launches()
    monkeypatch.setattr(process, "is_windows", lambda: False)
    monkeypatch.setattr(subprocess, "run", launches.run)
    run_command(["git", "status"])
    assert launches.commands == [["git", "status"]]


def test_run_tool_resolves_the_name_like_run_command(monkeypatch: pytest.MonkeyPatch) -> None:
    launches = windows(monkeypatch, {"npm-tool": NPM_TOOL_CMD})
    # 一覧の外の名前でも、Windows の解決は run_command と同じ resolve_command を通る
    run_tool(["npm-tool", "--version"])
    assert launches.commands == [[NPM_TOOL_CMD, "--version"]]


def test_run_tool_refuses_cmd_special_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    launches = windows(monkeypatch, {"npm-tool": NPM_TOOL_CMD})
    with pytest.raises(CommandError):
        run_tool(["npm-tool", "a&b"])
    assert launches.commands == []
