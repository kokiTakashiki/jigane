import ast
import json
import re
from pathlib import Path

import pytest
from dev_launch import run_tool
from ruamel.yaml import YAML

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


STORED_READERS = {
    "read_stored_file",
    "read_stored_file_if_present",
    "read_stored_text",
    "stored_entry_present",
}
STORED_FILE_RULE = ROOT / "ast-grep" / "rules" / "no-raw-read-of-stored-file.yml"


def stored_file_regex() -> str:
    rule = YAML(typ="safe").load(STORED_FILE_RULE.read_text(encoding="utf-8"))
    return str(rule["constraints"]["FILE"]["regex"])


def product_sources(*, include_storage: bool) -> list[ast.Module]:
    return [
        ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "jigane").rglob("*.py"))
        # 規則は入口の定義そのもの（jigane/storage/**）を対象から外している
        if include_storage or "storage" not in path.relative_to(ROOT).parts
    ]


def _passed_file_names(call: ast.Call) -> set[str]:
    """読みの入口の第二引数（位置でも name= でも）に渡した定数の名前。

    属性の参照は末尾の名前を取る。
    """
    arguments = [*call.args[1:2], *(k.value for k in call.keywords if k.arg == "name")]
    names: set[str] = set()
    for argument in arguments:
        if isinstance(argument, ast.Name):
            names.add(argument.id)
        elif isinstance(argument, ast.Attribute):
            names.add(argument.attr)
        else:
            # 文字列や式で渡した名前は定数の写しを持たないので、regex との突き合わせの対象にしない
            pass
    return names


@pytest.mark.whole_tree
def test_stored_file_rule_regex_covers_every_name_passed_to_the_readers() -> None:
    pattern = re.compile(stored_file_regex())
    passed: set[str] = set()
    for tree in product_sources(include_storage=False):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in STORED_READERS
            ):
                passed.update(_passed_file_names(node))
    assert passed, "保存先を読む呼び出しが 1 件も見つからない"
    uncovered = sorted(name for name in passed if not pattern.search(name))
    assert not uncovered, f"規則の regex に無い名前: {uncovered}"


@pytest.mark.whole_tree
def test_stored_file_rule_regex_names_are_defined_constants() -> None:
    match = re.fullmatch(r"\^\((.+)\)_FILE\$", stored_file_regex())
    assert match, "regex の形が ^(A|B)_FILE$ ではない"
    named = {f"{prefix}_FILE" for prefix in match.group(1).split("|")}
    defined: set[str] = set()
    for tree in product_sources(include_storage=True):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign | ast.AnnAssign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                defined.update(t.id for t in targets if isinstance(t, ast.Name))
    assert not (named - defined), f"定義の無い名前: {sorted(named - defined)}"
