"""GitHub CLI（gh）の呼び出しの唯一の入口。

gh は利用者の認証で書き込みもできる。jigane は読むだけの道具なので、起動してよい形を
`ALLOWED_SHAPES` の三つに限り、argv を組み立てた後、起動する前に照合する。
README の「jigane が呼ぶ gh のコマンド」の節は、この三つと同じ内容を持つ。
"""

import os
import re

from jigane.git import process
from jigane.git.environment import GIT_LOCATION_VARIABLES

PULL_REQUEST_FIELDS = "number,url,title,body,baseRefOid,headRefOid,author"

# 形の中の可変の部分。照合は、この印を右の正規表現に置き換えて行う
_NAME = r"(?!-)(?!\.+(?:/|$))[A-Za-z0-9._-]+"
_PLACEHOLDERS = {
    "<owner>/<repo>": f"{_NAME}/{_NAME}",
    "<番号>": r"[1-9][0-9]*",
    "<置き場>": r"(?!-).+",
}

# gh の第 2 引数以降の三つの形。README の一覧と一致することを検査が見る
ALLOWED_SHAPES: tuple[tuple[str, ...], ...] = (
    ("pr", "view", "<番号>", "-R", "<owner>/<repo>", "--json", PULL_REQUEST_FIELDS),
    ("repo", "clone", "<owner>/<repo>", "<置き場>", "--", "--bare", "--filter=blob:none"),
    (
        "api",
        "--method",
        "GET",
        "--paginate",
        "--slurp",
        "repos/<owner>/<repo>/pulls/<番号>/comments",
    ),
)

# gh api を書き込みにする指定。形の照合でも落ちるが、拒む理由を明示するために先に見る
_WRITE_LONG_FLAGS = ("--method", "--field", "--raw-field", "--input")
_WRITE_SHORT_FLAGS = ("-X", "-f", "-F")


class GhCommandNotAllowedError(ValueError):
    """許可リストに無い gh の呼び出し。gh は起動していない。"""


def _token_pattern(token: str) -> re.Pattern[str]:
    pattern = re.escape(token)
    for placeholder, expression in _PLACEHOLDERS.items():
        pattern = pattern.replace(re.escape(placeholder), expression)
    return re.compile(pattern)


_SHAPE_PATTERNS = tuple(
    tuple(_token_pattern(token) for token in shape) for shape in ALLOWED_SHAPES
)


def _is_write_flag(argument: str) -> bool:
    if argument.startswith(_WRITE_SHORT_FLAGS):
        return True
    return any(argument == flag or argument.startswith(f"{flag}=") for flag in _WRITE_LONG_FLAGS)


def _is_get_method(arguments: list[str], index: int) -> bool:
    # 許可の形が書く `--method GET` の二語だけを通す。
    # `--method=GET` は同じ意味でも形が違うので通さない
    return arguments[index] == "--method" and arguments[index + 1 : index + 2] == ["GET"]


def check_allowed(argv: list[str]) -> None:
    """argv が許可リストの形に合わなければ GhCommandNotAllowedError を投げる。"""
    if not argv or argv[0] != "gh":
        raise GhCommandNotAllowedError(f"gh の呼び出しではありません: {argv!r}")
    arguments = argv[1:]
    if arguments[:1] == ["api"] and any(
        _is_write_flag(argument) and not _is_get_method(arguments, index)
        for index, argument in enumerate(arguments[1:], start=1)
    ):
        raise GhCommandNotAllowedError(f"gh api は --method GET だけです: {argv!r}")
    for patterns in _SHAPE_PATTERNS:
        if len(patterns) != len(arguments):
            continue  # 引数の数が違う形とは、中身を見るまでもなく合わない
        pairs = zip(patterns, arguments, strict=True)
        if all(pattern.fullmatch(argument) for pattern, argument in pairs):
            return
    raise GhCommandNotAllowedError(f"許可リストに無い gh の呼び出しです: {argv!r}")


def _child_environment() -> dict[str, str]:
    # GH_HOST は問い合わせ先を、GH_REPO は既定のリポジトリを変える。jigane が受ける URL は
    # github.com だけなので固定する。GH_TOKEN などの認証の変数は消さない
    # gh repo clone は中で git を呼ぶので、git の場所の変数も外す（git 直の起動と同じ一覧）
    environment = {
        k: v for k, v in os.environ.items() if k != "GH_REPO" and k not in GIT_LOCATION_VARIABLES
    }
    environment["GH_HOST"] = "github.com"
    return environment


def _run(argv: list[str]) -> str:
    check_allowed(argv)
    return process.run_command(argv, env=_child_environment())


def view_pull_request(owner: str, repo: str, number: int) -> str:
    return _run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "-R",
            f"{owner}/{repo}",
            "--json",
            PULL_REQUEST_FIELDS,
        ]
    )


def clone_repository(owner: str, repo: str, destination: str) -> str:
    # blob は後から必要な分だけ取る。非公開のリポジトリも gh の認証で取れる
    return _run(
        [
            "gh",
            "repo",
            "clone",
            f"{owner}/{repo}",
            destination,
            "--",
            "--bare",
            "--filter=blob:none",
        ]
    )


def pull_request_comments(owner: str, repo: str, number: int) -> str:
    return _run(
        [
            "gh",
            "api",
            "--method",
            "GET",
            "--paginate",
            "--slurp",
            f"repos/{owner}/{repo}/pulls/{number}/comments",
        ]
    )
