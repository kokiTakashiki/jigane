import subprocess
from pathlib import Path

import pytest

from jigane.git import process
from jigane.git.process import (
    CommandError,
    _refuse_unlisted,
    child_environment,
    run_command,
    run_command_bytes,
)


def test_git_runs_in_the_working_tree_despite_inherited_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_command(["git", "init", "-q", str(tmp_path)])
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "nowhere"))
    top = run_command(["git", "rev-parse", "--show-toplevel"], cwd=tmp_path)
    assert Path(top.strip()).resolve() == tmp_path.resolve()


def test_non_git_commands_keep_the_git_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", "kept")
    # gh は孫として git を起こすため、git 以外の経路は GIT_DIR を消さない。一覧の外の名前は
    # run_command で起動できないので、環境を作る関数を直接呼ぶ
    assert child_environment(["gh", "pr", "view"], None) is None
    assert child_environment(["gh", "pr", "view"], {"GIT_DIR": "kept"}) == {"GIT_DIR": "kept"}


def test_command_error_message_has_command_and_directory(tmp_path: Path) -> None:
    # 置かれた場所が作業木かによらず失敗する副コマンドを使う
    with pytest.raises(CommandError) as raised:
        run_command(["git", "no-such-subcommand"], cwd=tmp_path)
    message = str(raised.value)
    assert "git no-such-subcommand" in message
    assert str(tmp_path) in message


@pytest.mark.whole_tree
def test_consent_prompts_end_with_the_marker_the_gui_matches() -> None:
    # GUI は標準エラーの末尾が CONSENT_PROMPT_END かで問いを見分ける。問いを出す側が
    # 文面を自前で書くと食い違うので、末尾は定数だけが持つ
    package = Path(__file__).resolve().parent.parent / "jigane"
    spelled = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if "（y/N）" in path.read_text(encoding="utf-8")
    ]

    assert spelled == ["git/process.py"]


def test_run_command_bytes_returns_undecoded_output(tmp_path: Path) -> None:
    run_command(["git", "init", "-q", str(tmp_path)])
    (tmp_path / "blob.bin").write_bytes(b"\xff\x00abc")
    run_command(["git", "add", "blob.bin"], cwd=tmp_path)

    assert (
        run_command_bytes(["git", "cat-file", "blob", ":blob.bin"], cwd=tmp_path) == b"\xff\x00abc"
    )


def test_run_command_bytes_failure_is_a_command_error(tmp_path: Path) -> None:
    with pytest.raises(CommandError) as raised:
        run_command_bytes(["git", "cat-file", "blob", "0" * 40], cwd=tmp_path)

    assert raised.value.returncode is not None


def test_failure_output_is_decoded_with_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(args: list[str], **_kwargs: object) -> object:
        raise subprocess.CalledProcessError(3, args, output=b"out\xff", stderr=b"err\xfe")

    monkeypatch.setattr(process, "run_resolved", failing)

    with pytest.raises(CommandError) as raised:
        run_command_bytes(["git", "status"])

    assert (raised.value.stdout, raised.value.stderr, raised.value.returncode) == (
        "out\ufffd",
        "err\ufffd",
        3,
    )


@pytest.mark.parametrize("name", ["git.sh", "git.py", "git.cmd", "gh.bat", "git.exe.sh"])
def test_executables_with_other_extensions_are_not_listed(name: str) -> None:
    with pytest.raises(CommandError):
        _refuse_unlisted([name])


@pytest.mark.parametrize(
    "name",
    ["git", "git.exe", "GIT.EXE", "Git", "C:\\Program Files\\Git\\bin\\git.exe", "/usr/bin/git"],
)
def test_git_names_are_listed_and_keep_git_isolation(name: str) -> None:
    _refuse_unlisted([name])
    assert child_environment([name], {"GIT_DIR": "x", "A": "b"}) == {
        "A": "b",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file:https:ssh",
    }


def test_git_script_keeps_the_git_variables() -> None:
    assert child_environment(["git.sh"], {"GIT_DIR": "x"}) == {"GIT_DIR": "x"}


def test_double_exe_suffix_is_not_listed() -> None:
    # .exe は一つだけ落とすので git.exe.exe は git.exe になり、一覧の外になる
    with pytest.raises(CommandError):
        _refuse_unlisted(["git.exe.exe"])


def test_refusal_message_names_the_allowed_executables() -> None:
    with pytest.raises(CommandError) as raised:
        _refuse_unlisted(["git.sh"])
    message = str(raised.value)
    assert "gh、git" in message
