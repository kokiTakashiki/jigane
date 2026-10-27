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
    assert scan_rule_ids(tmp_path, source) == ["no-git-stash-python"]


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
