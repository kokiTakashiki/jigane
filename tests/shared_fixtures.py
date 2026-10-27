"""全試験が使う共有の fixture。tests と tests_posix の conftest が差し込む。"""

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pr_support import BODY, URL, commit_all, git

from jigane.commands import context as cli_context
from jigane.git import process
from jigane.languages import registry
from jigane.storage.store import default_data_home


def pytest_configure() -> None:
    """試験を一件も走らせる前に、JIGANE_HOME が本物の置き場を指していないかを確かめる。

    各試験の fixture は JIGANE_HOME を tmp_path へ向けるが、plugin を読めなかった場合に備え、
    ゲートを走らせる側が向けた値を始めに見張る。xdist の作業者の起動でも同じ検査が走る。
    """
    fix = "JIGANE_HOME を一時のディレクトリへ向けて走らせてください（make test か just test）"
    value = os.environ.get("JIGANE_HOME")
    if not value:
        pytest.exit(f"JIGANE_HOME が未設定または空です。{fix}", returncode=4)
    real = default_data_home().resolve()
    given = Path(value).resolve()
    if given == real or real in given.parents:
        pytest.exit(
            f"JIGANE_HOME が本物の置き場（{real}）かその下を指しています。{fix}", returncode=4
        )


@pytest.fixture(autouse=True)
def _jigane_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """実際のホームを読み書きしないよう、全テストのデータの置き場を tmp_path 配下へ向ける。"""
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _language_states() -> Iterator[None]:
    """言語の状態は一度求めて保持されるので、テストをまたがないよう前後で捨てる。"""
    registry.set_states(None)
    yield
    registry.set_states(None)


@pytest.fixture(scope="session")
def repo_template(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    # git の起動がテストの所要の大半を占めるため、作業の木は一度だけ組み、各テストへ写す。
    # test_new_range の repo fixture を import する他のテストからも見えるよう、ここに置く
    root = tmp_path_factory.mktemp("repo_template") / "work"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/octo/hello.git")
    (root / "edit.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (root / "gone.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (root / "CODEOWNERS").write_text("*.py @octo\n", encoding="utf-8")
    commit_all(root, "init")
    git(root, "switch", "-c", "feature")
    (root / "edit.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
    commit_all(root, "change alpha\n\nbecause it is wrong")
    (root / "gone.py").unlink()
    (root / "new.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    head = commit_all(root, "swap files")
    # main が進んでも、変更前は merge-base のまま
    git(root, "switch", "main")
    (root / "other.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(root, "advance main")
    git(root, "switch", "feature")
    return {
        "root": str(root),
        "head": head,
        "merge_base": git(root, "merge-base", "main", "feature"),
    }


@pytest.fixture(scope="session")
def pr_template(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    # git の起動がテストの所要の大半を占めるため、PR の元になる木は一度だけ組み、各テストへ写す
    root = tmp_path_factory.mktemp("pr_template")
    origin = root / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    (origin / "edit.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (origin / "gone.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (origin / "old_name.py").write_text(
        "def gamma():\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n", encoding="utf-8"
    )
    # 中身の行が差分の見出しの形になるファイル
    (origin / "tricky.txt").write_text("-- a/x\nkeep\n", encoding="utf-8")
    (origin / ".jigane.toml").write_text('exclude = ["generated/*"]\n', encoding="utf-8")
    (origin / "docs").mkdir()
    (origin / "docs" / "design.md").write_text("# 設計\n", encoding="utf-8")
    (origin / "generated").mkdir()
    (origin / "generated" / "out.py").write_text("x = 1\n", encoding="utf-8")
    (origin / ".github").mkdir()
    (origin / ".github" / "CODEOWNERS").write_text(
        "* @octo/all\n*.py @octo/py  # 末尾の注釈\ndocs/ @octo/docs\ngone.py\n", encoding="utf-8"
    )
    commit_all(origin, "first")
    (origin / "edit.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
    commit_all(origin, "Tune alpha (#5)")
    base = git(origin, "rev-parse", "HEAD")
    git(origin, "mv", "old_name.py", "new_name.py")
    (origin / "edit.py").write_text("def alpha():\n    return 10\n", encoding="utf-8")
    (origin / "gone.py").unlink()
    (origin / "tricky.txt").write_text("++ b/x\nkeep\n", encoding="utf-8")
    (origin / "added.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    # 作者が PR の中で除外を外しても、読むのは変更前の設定
    (origin / ".jigane.toml").write_text("exclude = []\n", encoding="utf-8")
    (origin / "generated" / "out.py").write_text("x = 2\n", encoding="utf-8")
    head = commit_all(origin, "head")

    cache = root / "home" / "repositories" / "octo" / "hello.git"
    cache.parent.mkdir(parents=True)
    git(root, "clone", "--bare", "--no-local", str(origin), str(cache))
    return {"root": root, "base": base, "head": head}


def _fake_gh_api(args: list[str], state: dict[str, Any]) -> str:
    """偽の gh api。コメントのページを返す。"""
    assert "--slurp" in args
    return json.dumps(state["comment_pages"])


def _fake_gh_pr_view(state: dict[str, Any]) -> str:
    """偽の gh pr view。PR の本文、基点と先頭のコミットを返す。"""
    return json.dumps(
        {
            "number": 7,
            "url": URL,
            "title": state["title"],
            "body": state["body"],
            "baseRefOid": state["base"],
            "headRefOid": state["head"],
            "author": {"login": state["author"]},
        }
    )


@pytest.fixture
def pr(
    pr_template: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    shutil.copytree(pr_template["root"], tmp_path, symlinks=True, dirs_exist_ok=True)
    origin = tmp_path / "origin"
    # bare clone は雛形の origin の絶対パスを持つ。向け直さないと、雛形を読み書きしてしまう
    cache = tmp_path / "home" / "repositories" / "octo" / "hello.git"
    git(cache, "remote", "set-url", "origin", str(origin))
    base = pr_template["base"]
    head = pr_template["head"]
    state: dict[str, Any] = {
        "title": "支払い",
        "body": BODY,
        "base": base,
        "head": head,
        "origin": origin,
        # gh api のページごとの配列。--slurp は全ページを一つの配列に包む
        "comment_pages": [[]],
        "author": "dev",
    }
    real_run = process.run_command

    def fake_run(
        args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> str:
        if args[:2] == ["gh", "api"]:
            return _fake_gh_api(args, state)
        if args[0] == "gh":
            return _fake_gh_pr_view(state)
        return real_run(args, cwd, env=env)

    monkeypatch.setattr(process, "run_command", fake_run)
    return state


# autouse の偽物に差し替える前に、本物の read_answer を控える
REAL_READ_ANSWER = cli_context.read_answer


@pytest.fixture
def real_read_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """_terminal_with_consent が偽物に差し替えた read_answer を、本物へ戻す。"""
    monkeypatch.setattr("jigane.commands.context.read_answer", REAL_READ_ANSWER)


@pytest.fixture(autouse=True)
def _terminal_with_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    """run の同意の確認に、端末で y と答える偽物を既定で与える。同意の検査は個別に差し替える。"""
    monkeypatch.setattr("jigane.commands.context.stdio_is_terminal", lambda: True)
    monkeypatch.setattr("jigane.commands.context.read_answer", lambda _prompt: "y")
