"""git の呼び出しの引数を、`--end-of-options` で位置引数を守って組む。"""

from collections.abc import Sequence
from enum import StrEnum

from jigane.git.revision import CommitSha


class GitSubcommand(StrEnum):
    DIFF = "diff"
    ARCHIVE = "archive"
    LS_TREE = "ls-tree"
    MERGE_BASE = "merge-base"
    FETCH = "fetch"
    SHOW = "show"
    LOG = "log"


def build_git_command(
    subcommand: GitSubcommand,
    *,
    global_options: Sequence[str] = (),
    options: Sequence[str] = (),
    fixed_values: Sequence[str] = (),
    revisions: Sequence[CommitSha] = (),
    revision_paths: Sequence[tuple[CommitSha, str]] = (),
    pathspecs: Sequence[str] | None = None,
) -> list[str]:
    """次の並びの引数を返す。

    `git <全体のオプション…> <副コマンド> <オプション…> --end-of-options
    <固定の値…> <コミット…> <コミット:パス…>`
    pathspecs を渡したときだけ、末尾に `-- <パス…>` を足す（空の並びでも `--` は置く）。
    """
    if fixed_values and subcommand is not GitSubcommand.FETCH:
        raise ValueError(f"{subcommand} は固定の値を取りません")
    command = [
        "git",
        *global_options,
        subcommand.value,
        *options,
        "--end-of-options",
        *fixed_values,
    ]
    command.extend(str(revision) for revision in revisions)
    # `<コミット>:<パス>` は 1 個の位置引数。先頭が検査済みのコミットなので、
    # パスはオプションにならない
    command.extend(f"{revision}:{path}" for revision, path in revision_paths)
    if pathspecs is not None:
        command.extend(["--", *pathspecs])
    return command
