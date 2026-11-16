"""git の起動の入口。fsmonitor、hook、pager を起動させない指定を、引数と環境へ必ず付ける。"""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from jigane.git import process

if TYPE_CHECKING:
    from jigane.git.command_line import GitSubcommand

# jigane のパッケージのディレクトリ。導入した利用者か管理者しか書けず、他人が先回りして置けない
_PACKAGE_DIRECTORY = Path(__file__).resolve().parent


def no_hooks_path() -> Path:
    """存在しない絶対パス。git は hook を探して見つけず、何も起動しない（実測は git 2.54.0）。

    空のディレクトリは配布物から落ちうる。/dev/null は文書にあるが Windows での扱いが文書に無い。
    起動ごとの乱数で名前を決め、作らないので、レビュー対象のリポジトリが同じ名前を用意できない。
    """
    return _PACKAGE_DIRECTORY / f"no-hooks-{secrets.token_hex(16)}"


@dataclass(frozen=True)
class GitInvocation:
    """git の 1 回の起動。引数と環境を一緒に持つ。作るのは `git_invocation` だけ。"""

    subcommand: str
    arguments: tuple[str, ...]
    cwd: Path | None
    environment: dict[str, str] | None

    @property
    def argv(self) -> list[str]:
        return ["git", *self.arguments]


def git_invocation(
    subcommand: str,
    arguments: Sequence[str] = (),
    *,
    global_options: Sequence[str] = (),
    cwd: Path | None = None,
) -> GitInvocation:
    """全ての git の起動の入口。レビュー対象の設定から外のコマンドが起動する経路を塞ぐ。

    塞ぐのは、fsmonitor（core.fsmonitor=false）、hook（core.hooksPath を存在しないパスへ）、
    署名の確かめ（log.showSignature=false。log が gpg.program を起動しない）、
    pager（--no-pager）と、環境の GIT_TERMINAL_PROMPT と GIT_ALLOW_PROTOCOL である。
    塞ぐ `-c` は global_options の後ろに置き、呼ぶ側が同じ鍵を渡しても塞ぐ側が勝つ。
    filter（smudge、clean、process）は塞がない。jigane が走らせる副コマンドは作業木へ書き出さず、
    作業木のファイルを読み直しもしないので、filter を起動する経路が無い。
    diff の textconv と外部の diff は、build_git_command が --no-textconv と --no-ext-diff で塞ぐ。
    core.sshCommand、credential.helper、core.askPass、core.alternateRefsCommand は塞がない。
    これらが効くのは fetch だけで、fetch が走るのは gh が作ったキャッシュであり、
    その設定を書くのは jigane と gh だけである。塞ぐと利用者の大域の設定まで消える。
    """
    full = (
        *global_options,
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={no_hooks_path()}",
        "-c",
        "log.showSignature=false",
        "--no-pager",
        subcommand,
        *arguments,
    )
    return GitInvocation(
        subcommand=subcommand,
        arguments=full,
        cwd=cwd,
        environment=process.child_environment(["git"], None),
    )


def run_invocation(invocation: GitInvocation) -> str:
    """入口が作った起動をそのまま走らせる。argv と環境を分けて渡さず、入口の環境を必ず使う。"""
    return process.run_command(invocation.argv, cwd=invocation.cwd, env=invocation.environment)


# 入口の関数の名前。scripts/check_egress.py がこの一覧を読み、呼び出しを入口経由と数える
GIT_ENTRANCE_FUNCTIONS = frozenset({"git_command", "git_version"})


def git_command(subcommand: "GitSubcommand", *arguments: str) -> list[str]:
    """副コマンドと引数から、入口を通した argv を返す。`process.run_command` へそのまま渡せる。"""
    return git_invocation(subcommand.value, arguments).argv


def git_version() -> list[str]:
    """`git --version` の argv。副コマンドではないので `git_command` から分ける。"""
    return git_invocation("--version").argv
