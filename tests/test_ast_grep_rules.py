import json
from pathlib import Path

import pytest
from dev_launch import run_tool

from jigane.git.process import CommandError

ROOT = Path(__file__).resolve().parent.parent
# 検査の対象にこのファイル自身が掛からないよう、語を分けて組み立てる。
GIT = "gi" + "t"
STASH = "sta" + "sh"
PKILL = "pki" + "ll"


def scan_rule_ids(tmp_path: Path, source: str) -> list[str]:
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    try:
        output = run_tool(
            [
                "uv",
                "run",
                "--locked",
                "ast-grep",
                "scan",
                "-c",
                str(ROOT / "sgconfig.yml"),
                "--json=compact",
                str(tmp_path),
            ],
            cwd=ROOT,
        )
    except CommandError as error:
        # 違反が見つかると ast-grep は 1 で終わるが、標準出力に結果は出る。
        if not error.stdout:
            raise
        output = error.stdout
    return [match["ruleId"] for match in json.loads(output)]


@pytest.mark.parametrize(
    "source",
    [
        f'run(["{GIT}", "-C", d, "{STASH}"])\n',
        f'run_tool(["{GIT}", "{STASH}"])\n',
        f'os.system("{GIT} {STASH} pop")\n',
        f'os.system(f"{GIT} {STASH} {{mode}}")\n',
        f'run_tool(("{GIT}", "{STASH}"))\n',
    ],
)
def test_git_stash_calls_are_found(tmp_path: Path, source: str) -> None:
    found = scan_rule_ids(tmp_path, source)
    # ["git", "stash"] の形は、入口の外の argv の規則にも当たる
    assert "no-git-stash-python" in found
    assert set(found) <= {"no-git-stash-python", "no-git-argv-outside-entrance"}


@pytest.mark.parametrize(
    "source",
    [
        f'run(["{PKILL}", "x"])\n',
        f'os.system("{PKILL} -f x")\n',
        f'os.system(f"{PKILL} {{name}}")\n',
        f'run(("{PKILL}", "x"))\n',
    ],
)
def test_name_kill_calls_are_found(tmp_path: Path, source: str) -> None:
    assert scan_rule_ids(tmp_path, source) == ["no-name-kill-python"]


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('x = next(c for c in children if c.type == ":")\n', ["no-bare-next-generator"]),
        ('x = required(next((c for c in children if c.type == ":"), None))\n', []),
    ],
)
def test_next_over_a_generator_without_a_default_is_found(
    tmp_path: Path, source: str, found: list[str]
) -> None:
    assert scan_rule_ids(tmp_path, source) == found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        (f'run(["{GIT}", "status"])\n', ["no-git-argv-outside-entrance"]),
        (f'run(("{GIT}", "-C", d, "log"))\n', ["no-git-argv-outside-entrance"]),
        (f'run(["{GIT}", *args])\n', ["no-git-argv-outside-entrance"]),
        (f'run(["gh", "{GIT}"])\n', []),
        (f'x = "{GIT}"\n', []),
    ],
)
def test_git_argv_literals_are_found(tmp_path: Path, source: str, found: list[str]) -> None:
    assert scan_rule_ids(tmp_path, source) == found


def test_comments_and_docstrings_are_not_found(tmp_path: Path) -> None:
    source = (
        f"# {GIT} {STASH} と {PKILL} は使わない。\n"
        f'"""{GIT} {STASH} と {PKILL} の説明。"""\n'
        "\n"
        "\n"
        "def f() -> None:\n"
        f'    """{GIT} {STASH}"""\n'
    )
    assert scan_rule_ids(tmp_path, source) == []
