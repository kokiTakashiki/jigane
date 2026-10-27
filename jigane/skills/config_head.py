"""設定ファイルの冒頭の設定の宣言を、差分のシャードと反証役の資料に添える。

差分は変更の周りの数行しか渡さないため、ファイルの冒頭にある挙動を変える宣言
（justfile の `set` など）が資料に入らない。冒頭を知らずに設定ファイルの挙動を述べると、
宣言を読めば消える指摘が出る。
"""

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from jigane.git.tree_files import read_tree_bytes

# 取り出せないときに先頭から渡す行数と、取り出した冒頭の行数の上限
HEAD_MAX_LINES = 40

_MAKE_RULE = re.compile(r"^[A-Za-z0-9_%][^\s:=]*(\s+[^\s:=]+)*\s*:(?![:=])")
_TOML_TABLE = re.compile(r"^\s*\[+([^\]]+)\]+")
_JUST_HEAD = re.compile(r"^(set|import|mod|export|alias)\??\s")


def _first_lines(lines: list[str]) -> list[str]:
    return lines[:HEAD_MAX_LINES]


def _justfile_head(lines: list[str]) -> list[str]:
    return [line for line in lines if _JUST_HEAD.match(line)]


def _makefile_head(lines: list[str]) -> list[str]:
    """先頭の変数の定義から `.PHONY` などの特殊ターゲットまで。

    最初の通常のルールの手前で止める。
    """
    head: list[str] = []
    for line in lines:
        if _MAKE_RULE.match(line):
            # 特殊ターゲット（`.PHONY:`）は名前が「.」で始まるので、この正規表現に当たらない
            break
        head.append(line)
    return head


def _pyproject_head(lines: list[str]) -> list[str]:
    """`[build-system]` と `[tool.*]` の表。`[project]` など他の表は挙動の設定ではない。"""
    head: list[str] = []
    wanted = False
    for line in lines:
        table = _TOML_TABLE.match(line)
        if table is not None:
            name = table.group(1).strip()
            wanted = name in ("build-system", "tool") or name.startswith("tool.")
        if wanted:
            head.append(line)
    return head


# 設定ファイルのファイル名（ディレクトリを除く）と、冒頭の取り出し方。対象の一覧はここだけ
CONFIG_FILE_HEADS: dict[str, Callable[[list[str]], list[str]]] = {
    "justfile": _justfile_head,
    "Justfile": _justfile_head,
    ".justfile": _justfile_head,
    "Makefile": _makefile_head,
    "makefile": _makefile_head,
    "GNUmakefile": _makefile_head,
    "pyproject.toml": _pyproject_head,
    "setup.cfg": _first_lines,
    "tox.ini": _first_lines,
    "package.json": _first_lines,
    ".editorconfig": _first_lines,
}

HEAD_SECTION_INTRO = (
    "### 設定ファイルの冒頭の設定\n\n"
    "差分に含まれる設定ファイルの冒頭の宣言である。差分には出ないが、"
    "設定ファイルの挙動を左右する。"
)


def config_head(path: str, text: str) -> str | None:
    """設定ファイルの冒頭の設定の宣言。設定ファイルでなければ None。取り出せなければ先頭の行。"""
    extract = CONFIG_FILE_HEADS.get(Path(path).name)
    if extract is None:
        return None
    lines = text.splitlines()
    head = extract(lines)
    if not head:
        # 取り出し方が当たらない形のファイルでも、先頭は宣言が置かれる場所
        head = lines
    return "\n".join(_first_lines(head))


def config_heads_section(root: Path, paths: Iterable[str]) -> str:
    """paths のうち設定ファイルの冒頭を並べた節。添えるものが無ければ空文字列。"""
    parts: list[str] = []
    for path in sorted(paths):
        if Path(path).name not in CONFIG_FILE_HEADS:
            continue
        data = read_tree_bytes(root, path)
        if data is None:
            continue
        try:
            head = config_head(path, data.decode("utf-8"))
        except UnicodeDecodeError:
            continue
        if head:
            parts.append(f"=== {path} （冒頭の設定） ===\n{head}\n")
    if not parts:
        return ""
    return HEAD_SECTION_INTRO + "\n\n" + "\n".join(parts)
