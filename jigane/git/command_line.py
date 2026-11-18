"""git の呼び出しの引数を、`--end-of-options` で位置引数を守って組む。"""

from collections.abc import Sequence
from enum import StrEnum

from jigane.git.invocation import GitInvocation, git_invocation
from jigane.git.revision import CommitSha


class GitSubcommand(StrEnum):
    DIFF = "diff"
    CAT_FILE = "cat-file"
    LS_TREE = "ls-tree"
    MERGE_BASE = "merge-base"
    FETCH = "fetch"
    SHOW = "show"
    LOG = "log"
    LS_FILES = "ls-files"
    SUBMODULE = "submodule"
    REV_PARSE = "rev-parse"
    SYMBOLIC_REF = "symbolic-ref"
    REMOTE = "remote"
    REV_LIST = "rev-list"
    CHECK_ATTR = "check-attr"


def build_git_invocation(
    subcommand: GitSubcommand,
    *,
    global_options: Sequence[str] = (),
    options: Sequence[str] = (),
    fixed_values: Sequence[str] = (),
    revisions: Sequence[CommitSha] = (),
    revision_paths: Sequence[tuple[CommitSha, str]] = (),
    pathspecs: Sequence[str] | None = None,
) -> GitInvocation:
    """次の並びの引数を返す。

    `git <全体のオプション…> <副コマンド> <オプション…> --end-of-options
    <固定の値…> <コミット…> <コミット:パス…>`
    FETCH のときだけ `--upload-pack=git-upload-pack` を、DIFF、SHOW、LOG のときだけ
    `--no-ext-diff --no-textconv` を、<オプション…> の前に足す。
    固定の値は FETCH（リモート名）と CHECK_ATTR（属性の名前）だけが取る。
    pathspecs を渡したときだけ、末尾に `-- <パス…>` を足す（空の並びでも `--` は置く）。
    """
    if fixed_values and subcommand not in (GitSubcommand.FETCH, GitSubcommand.CHECK_ATTR):
        raise ValueError(f"{subcommand} は固定の値を取りません")
    command = [
        # 外の比較コマンドと textconv の変換コマンドを、リポジトリの設定が指していても起動しない
        *(
            ["--no-ext-diff", "--no-textconv"]
            if subcommand in (GitSubcommand.DIFF, GitSubcommand.SHOW, GitSubcommand.LOG)
            else []
        ),
        *options,
        # remote.<名前>.uploadpack は名前が任意で -c では潰せない。引数で明示すると設定は使われない
        *(["--upload-pack=git-upload-pack"] if subcommand is GitSubcommand.FETCH else []),
        "--end-of-options",
        *fixed_values,
    ]
    command.extend(str(revision) for revision in revisions)
    # `<コミット>:<パス>` は 1 個の位置引数。先頭が検査済みのコミットなので、
    # パスはオプションにならない
    command.extend(f"{revision}:{path}" for revision, path in revision_paths)
    if pathspecs is not None:
        command.extend(["--", *pathspecs])
    return git_invocation(subcommand.value, command, global_options=global_options)


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
    """build_git_invocation の argv を返す。呼ぶ側が argv を process.run_command へ渡すときに使う。

    封じ込めの引数は argv に入り、子の環境の指定は run_command の child_environment が付ける。
    """
    return build_git_invocation(
        subcommand,
        global_options=global_options,
        options=options,
        fixed_values=fixed_values,
        revisions=revisions,
        revision_paths=revision_paths,
        pathspecs=pathspecs,
    ).argv
