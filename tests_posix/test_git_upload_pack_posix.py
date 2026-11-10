from pathlib import Path

import pytest
from pr_support import git

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command


def _upload_pack_fetch(tmp_path: Path, *, with_delimiter: bool) -> Path:
    """`--upload-pack=<コマンド>` を固定の値に渡して fetch を走らせ、印の場所を返す。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    marker = tmp_path / "marker"
    command = build_git_command(
        GitSubcommand.FETCH, fixed_values=[f"--upload-pack=touch {marker}", str(bare)]
    )
    assert command.index("--end-of-options") < command.index(f"--upload-pack=touch {marker}")
    if not with_delimiter:
        command.remove("--end-of-options")
    with pytest.raises(process.CommandError):
        process.run_command(command, cwd=repo)
    return marker


def test_dash_value_is_read_as_remote_not_option(tmp_path: Path) -> None:
    # 区切りがあれば git は値をリモートの名前として読み、コマンドは走らない
    assert not _upload_pack_fetch(tmp_path, with_delimiter=True).exists()


def test_dash_value_runs_as_option_without_delimiter(tmp_path: Path) -> None:
    # 区切りを外すと upload-pack としてコマンドが走る。上の試験が区切りを本当に見ている証拠
    assert _upload_pack_fetch(tmp_path, with_delimiter=False).exists()
