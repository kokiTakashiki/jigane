"""機能、範囲、PR、設計書のレビューを作り、run を偽の claude で走らせた起動の記録を返す。"""

import io
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from fakes import write_fake_claude_wrapper
from pr_support import URL, commit_all, git

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.git import process
from jigane.git.process import CLAUDE_BINARY_ENVIRONMENT
from jigane.groups.definitions import GroupId
from jigane.languages.grammars import CACHE_ENV
from jigane.llm_runs.claude import REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES
from jigane.storage.store import ReviewKind

FAKE_SCRIPT = Path(__file__).parent / "claude_inputs_fake.py"
FILES = ("src/calc.py", "src/util.py", "tests/test_calc.py")
REVIEW_NAME = "local/proj:probe"
MCP_SERVER = "fakesrv"
DESIGN_DOCUMENT = "docs/design.md"
ATTACHMENT = "docs/reports/memo.md"
# 記録用の包みの sh が足し引きする変数で、jigane の渡す情報ではない
WRAPPER_ENVIRONMENT = frozenset({"PWD", "OLDPWD", "SHLVL", "_"})


class Launch(dict[str, Any]):
    """起動の記録の 1 件。kind はレビューの種類、group は run の群、role は見分けた役。"""

    @property
    def kind(self) -> ReviewKind:
        return ReviewKind(self["kind"])

    @property
    def group(self) -> GroupId:
        return GroupId(self["group"])

    @property
    def role(self) -> str:
        return str(self["role"])


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_repository(root: Path) -> None:
    root.mkdir()
    git(root, "init", "-b", "main")
    _write(root / "src/calc.py", "def add(a, b):\n    return a + b\n")
    _write(root / "src/util.py", "def twice(x):\n    return x * 2\n")
    test_source = "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    _write(root / "tests/test_calc.py", test_source)
    git(root, "add", ".")
    git(root, "commit", "-m", "base")


def _run(
    arguments: list[str],
    kind: ReviewKind,
    group: GroupId,
    work: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[Launch]:
    records = work / f"records-{kind}-{group}-{len(list(work.glob('records-*')))}"
    records.mkdir()
    parent = work / f"parent-{records.name}.json"
    wrapper_directory = work / f"bin-{records.name}"
    wrapper_directory.mkdir()
    wrapper = write_fake_claude_wrapper(
        wrapper_directory, script=FAKE_SCRIPT, leading_arguments=(str(records), str(parent))
    )
    monkeypatch.setenv(CLAUDE_BINARY_ENVIRONMENT, str(wrapper))
    # 親の環境は、jigane の起動の直前の os.environ。偽の包みを指す変数は親の側に含める
    # jigane は文法の置き場の変数を自分のプロセスに置く。前の起動が置いた値を消し、
    # この起動で足されたことが記録に現れるようにする
    monkeypatch.delenv(CACHE_ENV, raising=False)
    parent.write_text(json.dumps(dict(os.environ)), encoding="utf-8")
    assert main(arguments) == EXIT_OK, arguments
    return [
        Launch(
            json.loads(path.read_text(encoding="utf-8")),
            group=group.value,
            kind=kind.value,
            home=os.environ["JIGANE_HOME"],
        )
        for path in sorted(records.glob("[0-9]*.json"))
    ]


def _make_range_repository(root: Path) -> str:
    """コミットを 3 つ積み、先頭の手前までを背景にする範囲 `<base>..<head>` を返す。"""
    root.mkdir()
    git(root, "init", "-b", "main")
    _write(root / "src/calc.py", "def add(a, b):\n    return a + b\n")
    base = commit_all(root, "first")
    _write(
        root / "src/calc.py",
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n",
    )
    commit_all(root, "add sub")
    _write(root / "src/util.py", "def twice(x):\n    return x * 2\n")
    return f"{base}..{commit_all(root, 'add util')}"


def _make_design_repository(root: Path) -> None:
    root.mkdir()
    git(root, "init", "-b", "main")
    _write(root / "src/calc.py", "def add(a, b):\n    return a + b\n")
    design = (
        "# 計算器の設計\n\n## 方針\n\n`src/calc.py` に add を置く。\n\n## 検証\n\n試験を書く。\n"
    )
    _write(root / DESIGN_DOCUMENT, design)
    _write(root / ATTACHMENT, "# 検証メモ\n\n足し算を確かめた。\n")
    commit_all(root, "first")


def _make_pull_request(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ボットのコメント、過去の PR、CODEOWNERS、本文が参照する文書を持つ PR を用意する。"""
    origin = home.parent / "gh_origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    _write(origin / "edit.py", "def alpha():\n    return 1\n")
    _write(origin / ".github/CODEOWNERS", "* @octo/all\n*.py @octo/py\n")
    _write(origin / "docs/design.md", "# 設計\n")
    commit_all(origin, "first")
    _write(origin / "edit.py", "def alpha():\n    return 2\n")
    base = commit_all(origin, "Tune alpha (#5)")
    _write(origin / "edit.py", "def alpha():\n    return 10\n")
    _write(origin / "added.py", "def delta():\n    return 4\n")
    head = commit_all(origin, "head")
    cache = home / "repositories" / "octo" / "hello.git"
    cache.parent.mkdir(parents=True)
    git(home.parent, "clone", "--bare", "--no-local", str(origin), str(cache))
    comment = {
        "id": 101,
        "user": {"login": "review-bot", "type": "Bot"},
        "body": "戻り値を見直す",
        "path": "edit.py",
        "original_commit_id": base,
        "original_line": 2,
        "original_start_line": None,
        "in_reply_to_id": None,
        "subject_type": "line",
        "html_url": f"{URL}#discussion_r101",
    }
    view = {
        "number": 7,
        "url": URL,
        "title": "支払い",
        "body": f"支払いを足す。設計は {DESIGN_DOCUMENT} にある",
        "baseRefOid": base,
        "headRefOid": head,
        "author": {"login": "dev"},
    }
    real_run = process.run_command

    def fake_run(
        args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> str:
        if args[:2] == ["gh", "api"]:
            return json.dumps([[comment]])
        if args[0] == "gh":
            return json.dumps(view)
        return real_run(args, cwd, env=env)

    monkeypatch.setattr(process, "run_command", fake_run)


def _new_review(arguments: list[str]) -> str:
    """`jigane new … --json` でレビューを作り、その名前を返す。"""
    out = io.StringIO()
    with redirect_stdout(out):
        assert main([*arguments, "--json"]) == EXIT_OK, arguments
    return str(json.loads(out.getvalue())["id"])


@contextmanager
def recorded_launches(work: Path) -> Iterator[list[Launch]]:
    """4 種類のレビューを作り、macro、micro、security を走らせる。機能は MCP ありも走らせる。"""
    monkeypatch = pytest.MonkeyPatch()
    launches: list[Launch] = []
    try:
        home = work / "home"
        monkeypatch.setenv("JIGANE_HOME", str(home))
        monkeypatch.setattr("jigane.commands.context.stdio_is_terminal", lambda: True)
        monkeypatch.setattr("jigane.commands.context.read_answer", lambda _prompt: "y")
        # 除かれる変数が親にあるときだけ、除いたことが記録に現れる
        for name in REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES:
            monkeypatch.setenv(name, "dummy")
        repository = work / "proj"
        _make_repository(repository)
        monkeypatch.chdir(repository)
        assert main(["new", "files", *FILES, "--name", "probe"]) == EXIT_OK
        launches += _run_groups(REVIEW_NAME, ReviewKind.FEATURE, work, monkeypatch)
        # MCP の案内とサーバの定義は、どの種類のレビューでも同じ組み立てを通る。
        # そのため機能のレビューだけで走らせる
        servers = {MCP_SERVER: {"command": "no-such-mcp-server-binary", "args": ["--x"]}}
        mcp_json = home / "mcp.json"
        _write(mcp_json, json.dumps({"mcpServers": servers}))
        mcp_json.chmod(0o600)
        launches += _run(
            ["run", REVIEW_NAME, "security", "--mcps", MCP_SERVER],
            ReviewKind.FEATURE,
            GroupId.SECURITY,
            work,
            monkeypatch,
        )

        range_repository = work / "proj_range"
        monkeypatch.chdir(work)
        span = _make_range_repository(range_repository)
        monkeypatch.chdir(range_repository)
        name = _new_review(["new", "range", span, "--name", "span"])
        launches += _run_groups(name, ReviewKind.RANGE, work, monkeypatch)

        design_repository = work / "proj_doc"
        monkeypatch.chdir(work)
        _make_design_repository(design_repository)
        monkeypatch.chdir(design_repository)
        name = _new_review(
            ["new", "design-doc", DESIGN_DOCUMENT, "--name", "plan", "--with", ATTACHMENT]
        )
        launches += _run_groups(name, ReviewKind.DESIGN_DOC, work, monkeypatch)
        # 付属の文書が無いと、設計書の全文の見出しが「主の設計書」に替わらない
        name = _new_review(["new", "design-doc", DESIGN_DOCUMENT, "--name", "plain"])
        launches += _run(
            ["run", name, "macro"], ReviewKind.DESIGN_DOC, GroupId.MACRO, work, monkeypatch
        )

        _make_pull_request(home, monkeypatch)
        # 指摘の根拠が PR の変更の外にあると捨てられ、検証役が起動しない
        monkeypatch.setenv("FAKE_PATH", "edit.py")
        monkeypatch.chdir(work)
        name = _new_review(["new", "pr", URL])
        launches += _run_groups(name, ReviewKind.PULL_REQUEST, work, monkeypatch)
        yield launches
    finally:
        monkeypatch.undo()


def _run_groups(
    name: str, kind: ReviewKind, work: Path, monkeypatch: pytest.MonkeyPatch
) -> list[Launch]:
    launches: list[Launch] = []
    for group in (GroupId.MACRO, GroupId.MICRO, GroupId.SECURITY):
        launches += _run(["run", name, group.value], kind, group, work, monkeypatch)
    return launches
