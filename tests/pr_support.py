import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

URL = "https://github.com/octo/hello/pull/7"
BODY = "支払いを足す"


def isolated_git_environment() -> dict[str, str]:
    # 利用者の git 設定と認証の補助、親から漏れた GIT_* に依らず、同じ結果にする
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
    )
    return environment


def remove_tree(path: Path) -> None:
    """git の木を消す。Windows の読み取り専用のオブジェクトは、書き込み可に直して消す。"""

    def retry(function: Callable[[str], object], target: str, _: BaseException) -> None:
        Path(target).chmod(stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=retry)


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        env=isolated_git_environment(),
        check=False,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if completed.returncode != 0:
        # 失敗の原因は git の標準エラー出力にしか出ない。捨てずに例外へ載せる
        message = (
            f"git {' '.join(args)} が終了コード {completed.returncode} で失敗: {completed.stderr}"
        )
        raise RuntimeError(message)
    return completed.stdout.strip()


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def advance_head(pr: dict[str, Any]) -> None:
    origin = pr["origin"]
    # edit.py の内容を変え、added.py を消し、新しい箇所として extra.py を足す
    (origin / "edit.py").write_text("def alpha():\n    return 11\n", encoding="utf-8")
    (origin / "added.py").unlink()
    (origin / "extra.py").write_text("def epsilon():\n    return 5\n", encoding="utf-8")
    pr["head"] = commit_all(origin, "fix")


def grow_head(pr: dict[str, Any]) -> None:
    # 一覧の行（ファイル数、行数）が変わるよう、新しいファイルを足して先の commit を進める
    origin = pr["origin"]
    (origin / "grown.py").write_text(
        "def zeta():\n    return 6\n\n\ndef eta():\n    return 7\n", encoding="utf-8"
    )
    pr["head"] = commit_all(origin, "grow")
