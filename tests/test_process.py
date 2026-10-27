from pathlib import Path

import pytest

from jigane.git.process import CommandError, child_environment, run_command


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
