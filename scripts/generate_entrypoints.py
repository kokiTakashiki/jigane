"""entrypoints.toml から Makefile と justfile を生成する。標準ライブラリだけで動く。"""

import argparse
import importlib.util
import re
import sys
import tomllib
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFINITION = ROOT / "entrypoints.toml"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


class Placeholder(Enum):
    ARGS = "args"
    INVOCATION_DIR = "invocation_dir"
    PROJECT_DIR = "project_dir"
    # [entrypoints] の subproject が名指す、この木の下の別の木。そこにも同じ生成器の入口がある
    SUBPROJECT_DIR = "subproject_dir"
    SUBPROJECT_TASK = "subproject_task"
    # この木の別の作業を呼ぶ道具の名前。外の道具の設定へ値として渡す用
    SELF_TASK = "self_task"


SUBPROJECT_PLACEHOLDERS = {Placeholder.SUBPROJECT_DIR, Placeholder.SUBPROJECT_TASK}
SUBPROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def make_placeholders(subproject: str) -> dict[Placeholder, str]:
    directory = f'"$(PROJECT_DIR){subproject}"'
    return {
        Placeholder.ARGS: "$(ARGS)",
        Placeholder.INVOCATION_DIR: '"$(CURDIR)"',
        Placeholder.PROJECT_DIR: '"$(PROJECT_DIR)"',
        Placeholder.SUBPROJECT_DIR: directory,
        Placeholder.SUBPROJECT_TASK: f"$(MAKE) --no-print-directory -C {directory}",
        Placeholder.SELF_TASK: "make",
    }


def just_placeholders(subproject: str) -> dict[Placeholder, str]:
    directory = f'justfile_directory() / "{subproject}"'
    return {
        Placeholder.ARGS: "{{quote(args)}}",
        Placeholder.INVOCATION_DIR: "{{quote(invocation_directory())}}",
        Placeholder.PROJECT_DIR: "{{quote(justfile_directory())}}",
        Placeholder.SUBPROJECT_DIR: f"{{{{quote({directory})}}}}",
        Placeholder.SUBPROJECT_TASK: f'just --justfile {{{{quote({directory} / "justfile")}}}}',
        Placeholder.SELF_TASK: "just",
    }


JUST_GIT_WORKTREE_TASK = "_require-git-worktree"
JUST_TOOLS_TASK = "_require-tools"
JUST_LEGACY_TASK = "_warn-legacy-path"
JUST_ENV_TASK = "_require-subproject-env"


def _git_location_variables() -> tuple[str, ...]:
    # jigane のパッケージを import せず、定義の一か所（jigane/git/environment.py）をパスから読む
    spec = importlib.util.spec_from_file_location(
        "environment", ROOT / "jigane" / "git" / "environment.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("jigane/git/environment.py を読めない")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.GIT_LOCATION_VARIABLES)


# 親の hook や IDE から渡る向き先の変数を外して git を起こす。外さないと別の木を指して失敗する
GIT_LOCATION_VARIABLES = _git_location_variables()
UNIX_GIT = "env " + " ".join(f"-u {name}" for name in GIT_LOCATION_VARIABLES) + " git"
# PowerShell には env -u が無いため、外す文を前に置き、この子プロセスの中だけで消す
WINDOWS_GIT_PREFIX = "".join(
    f"Remove-Item Env:{name} -ErrorAction SilentlyContinue; " for name in GIT_LOCATION_VARIABLES
)


def git_worktree_lines(guide: "CloneGuide", command: str) -> list[str]:
    """案内の各行を返す。そのまま貼れるよう、手順は行頭を字下げした全文にする。

    git の作業木でない木（ZIP や写し）で、git の生の文面の代わりに出す。
    取得の URL と submodule の要否は、公開の範囲が木ごとに違うため entrypoints.toml が持つ。
    """
    clone = "git clone --recurse-submodules" if guide.submodule else "git clone"
    lines = ["  x git の作業木ではありません。次の手順で取り出し直してください。"]
    if guide.submodule:
        lines.append(f"    GitHub の ZIP では {guide.submodule} が入らないため、動きません。")
    return [*lines, f"    {clone} {guide.url}", "    cd jigane", f"    {command} setup"]


def shell_echo_chain(guide: "CloneGuide", command: str, quote: str) -> str:
    """行ごとの echo を並べる。引用符は make が二重、just が単一。"""
    return shell_echo_chain_of(git_worktree_lines(guide, command), quote)


def shell_echo_chain_of(lines: list[str], quote: str) -> str:
    return "; ".join(f"echo {quote}{line}{quote} >&2" for line in lines)


def powershell_write_chain(guide: "CloneGuide", command: str) -> str:
    return "; ".join(
        f"[Console]::Error.WriteLine('{line}')" for line in git_worktree_lines(guide, command)
    )


def env_missing_line(command: str) -> str:
    return f"  x cli の環境がありません。先に {command} setup を走らせてください。"


def legacy_lines(legacy: str, remove: str) -> list[str]:
    return [
        (
            f"  ! 根に旧い {legacy}/ の残り（追跡外のファイル）があります。"
            f"消してから使ってください: {remove}"
        )
    ]


# 版は `<道具> --version` の出力の最初の「数.数.数」から読む。比較は awk（sh）と
# [version]（PowerShell）で生成物の中に書く。cli のスクリプトに置くと、
# setup の前（cli の環境が無い間）に走らせられない。
SH_VERSION_READ = (
    "sed -n 's/[^0-9]*\\([0-9][0-9]*\\.[0-9][0-9]*\\.[0-9][0-9]*\\).*/\\1/p' | head -n 1"
)
POWERSHELL_VERSION_PATTERN = r"\d+\.\d+\.\d+"


def sh_tool_snippet(tool: "Tool") -> str:
    """sh で道具の有無と版を確かめる文の並び。make に埋めるときは `$` を二重にする。"""
    missing = f'echo "  x {tool.name} が見つかりません。{tool.update}" >&2; exit 1'
    find = f"command -v {tool.name} >/dev/null 2>&1 || {{ {missing}; }}"
    if not tool.verified:
        return find
    old = (
        f'echo "  x {tool.name} が古いです（${{v}}）。{tool.verified} 以上に更新してください: '
        f'{tool.update}" >&2; exit 1'
    )
    unverified = (
        f'echo "! {tool.name} ${{v}} は確かめていない版です。'
        f'動かないときは {tool.verified} 以上に更新してください" >&2'
    )
    read = f"v=$({tool.name} --version 2>&1 | {SH_VERSION_READ})"
    # 終了コード 2 は known_broken 以下、1 は verified 未満、0 は verified 以上
    compare = (
        'awk -v have="$v" -v broken='
        + tool.known_broken
        + " -v verified="
        + tool.verified
        + ' \'function cmp(x, y,   a, b, i) { split(x, a, "."); split(y, b, "."); '
        "for (i = 1; i <= 3; i++) { if (a[i] + 0 > b[i] + 0) return 1; "
        "if (a[i] + 0 < b[i] + 0) return -1 } return 0 } "
        "BEGIN { if (cmp(have, broken) <= 0) exit 2; if (cmp(have, verified) < 0) exit 1; "
        "exit 0 }'"
    )
    branch = (
        f"if [ $r -eq 2 ]; then {old}; "
        f"elif [ $r -eq 1 ]; then {unverified}; "
        "else :; fi"  # 確かめた版以上は、黙って続ける
    )
    return f"{find}; {read}; {compare}; r=$?; {branch}"


def powershell_tool_snippet(tool: "Tool") -> str:
    missing = (
        f"[Console]::Error.WriteLine('  x {tool.name} が見つかりません。{tool.update}'); exit 1"
    )
    find = f"if (-not (Get-Command {tool.name} -ErrorAction SilentlyContinue)) {{ {missing} }}"
    if not tool.verified:
        return find
    old = (
        f"[Console]::Error.WriteLine('  x {tool.name} が古いです（' + $m.Value + '）。"
        f"{tool.verified} 以上に更新してください: {tool.update}'); exit 1"
    )
    unverified = (
        f"[Console]::Error.WriteLine('! {tool.name} ' + $m.Value + ' は確かめていない版です。"
        f"動かないときは {tool.verified} 以上に更新してください')"
    )
    read = (
        f"$m = [regex]::Match(({tool.name} --version 2>&1 | Out-String), "
        f"'{POWERSHELL_VERSION_PATTERN}')"
    )
    return (
        f"{find}; {read}; "
        f"if (-not $m.Success -or [version]$m.Value -le [version]'{tool.known_broken}') "
        f"{{ {old} }} "
        f"elseif ([version]$m.Value -lt [version]'{tool.verified}') {{ {unverified} }}"
    )


JUST_GUARD_TASKS = {
    "require_git_worktree": JUST_GIT_WORKTREE_TASK,
    "require_tools": JUST_TOOLS_TASK,
    "warn_legacy_path": JUST_LEGACY_TASK,
    "require_subproject_env": JUST_ENV_TASK,
}


def _required_guide(facts: "Facts") -> "CloneGuide":
    if facts.clone_guide is None:
        raise ValueError("require_git_worktree は clone_url が要る")
    return facts.clone_guide


def make_guard_defines(facts: "Facts", tasks: tuple["Task", ...]) -> list[str]:
    """使う作業がある確かめだけを make の define にする。"""
    lines: list[str] = []
    executable = f"$(PROJECT_DIR){facts.subproject}/.venv/bin/{facts.subproject_executable}"
    if any(task.require_git_worktree for task in tasks):
        guide = _required_guide(facts)
        lines += [
            "# 根が git の作業木でなければ、取り出し直しの案内を出して止まる。",
            "define require_git_worktree",
            (
                f'@[ "$$({UNIX_GIT} -C "$(PROJECT_DIR)" rev-parse'
                ' --is-inside-work-tree 2>/dev/null)"'
                " = true ] || { \\"
            ),
            f"\t{shell_echo_chain(guide, 'make', chr(34))}; exit 1; }}",
            "endef",
            "",
        ]
    if any(task.require_tools for task in tasks):
        statements = [sh_tool_snippet(tool).replace("$", "$$") for tool in facts.tools]
        lines += [
            "# 外の道具が無い、または最低の版より古ければ、更新の案内を出して止まる。",
            "define require_tools",
            *(f"@{statement}" for statement in statements),
            "endef",
            "",
        ]
    if any(task.warn_legacy_path for task in tasks):
        legacy = facts.legacy_path
        echo = shell_echo_chain_of(legacy_lines(legacy, f"rm -r {legacy}"), chr(34))
        lines += [
            f"# 根に旧い {legacy}/ の残りがあれば、消す手順を知らせる。消さず、止めない。",
            "define warn_legacy_path",
            (
                f'@[ -z "$$({UNIX_GIT} -C "$(PROJECT_DIR)"'
                f' ls-files --others -- {legacy} 2>/dev/null)" ]'
                f" || {{ {echo}; }}"
            ),
            "endef",
            "",
        ]
    if any(task.require_subproject_env for task in tasks):
        lines += [
            "# subproject の環境が無ければ、先に setup を走らせる案内を出して止まる。",
            "define require_subproject_env",
            f'@[ -x "{executable}" ] || {{ echo "{env_missing_line("make")}" >&2; exit 1; }}',
            "endef",
            "",
        ]
    return lines


def just_guard_recipes(facts: "Facts", tasks: tuple["Task", ...]) -> str:
    """作業の前に走らせる確かめのレシピ。使う作業がある確かめだけを書く。"""
    legacy = facts.legacy_path
    executable = facts.subproject_executable
    directory = "justfile_directory()"
    unix_env = f'{{{{quote({directory} / "{facts.subproject}" / ".venv/bin/{executable}")}}}}'
    windows_env = (
        f'{{{{quote({directory} / "{facts.subproject}" / ".venv/Scripts/{executable}.exe")}}}}'
    )
    # 案内を使う作業が無ければ案内は組まない。作業があって型が無い定義は、読む時点で誤りにしてある
    guide = _required_guide(facts) if any(task.require_git_worktree for task in tasks) else None
    unix_worktree = shell_echo_chain(guide, "just", "'") if guide else ""
    windows_worktree = powershell_write_chain(guide, "just") if guide else ""
    unix_tools = "; ".join(sh_tool_snippet(tool) for tool in facts.tools)
    windows_tools = "; ".join(powershell_tool_snippet(tool) for tool in facts.tools)
    unix_legacy = shell_echo_chain_of(legacy_lines(legacy, f"rm -r {legacy}"), "'")
    windows_legacy = "; ".join(
        f"[Console]::Error.WriteLine('{line}')"
        for line in legacy_lines(legacy, f"Remove-Item -Recurse -Force {legacy}")
    )
    blocks = {
        "require_git_worktree": f"""\
# 根が git の作業木でなければ、取り出し直しの案内を出して止まる。
[unix]
{JUST_GIT_WORKTREE_TASK}:
    [ "$({UNIX_GIT} -C {{{{quote(justfile_directory())}}}} rev-parse --is-inside-work-tree 2>/dev/null)" = true ] || {{ {unix_worktree}; exit 1; }}

[windows]
{JUST_GIT_WORKTREE_TASK}:
    if (($({WINDOWS_GIT_PREFIX}git -C {{{{quote(justfile_directory())}}}} rev-parse --is-inside-work-tree 2>$null)) -ne 'true') {{ {windows_worktree}; exit 1 }}
""",  # noqa: E501 生成する just の 1 行が長く、折ると生成物の中身が変わる
        "require_tools": f"""\
# 外の道具が無い、または最低の版より古ければ、更新の案内を出して止まる。
[unix]
{JUST_TOOLS_TASK}:
    {unix_tools}

[windows]
{JUST_TOOLS_TASK}:
    {windows_tools}
""",
        "warn_legacy_path": f"""\
# 根に旧い {legacy}/ の残りがあれば、消す手順を知らせる。消さず、止めない。
[unix]
{JUST_LEGACY_TASK}:
    [ -z "$({UNIX_GIT} -C {{{{quote(justfile_directory())}}}} ls-files --others -- {legacy} 2>/dev/null)" ] || {{ {unix_legacy}; }}

[windows]
{JUST_LEGACY_TASK}:
    if ($({WINDOWS_GIT_PREFIX}git -C {{{{quote(justfile_directory())}}}} ls-files --others -- {legacy} 2>$null)) {{ {windows_legacy} }}
""",  # noqa: E501 生成する just の 1 行が長く、折ると生成物の中身が変わる
        "require_subproject_env": f"""\
# cli の環境が無ければ、先に setup を走らせる案内を出して止まる。
[unix]
{JUST_ENV_TASK}:
    [ -x {unix_env} ] || {{ echo '{env_missing_line("just")}' >&2; exit 1; }}

[windows]
{JUST_ENV_TASK}:
    if (-not (Test-Path {windows_env})) {{ [Console]::Error.WriteLine('{env_missing_line("just")}'); exit 1 }}
""",  # noqa: E501 生成する just の 1 行が長く、折ると生成物の中身が変わる
    }
    return "\n".join(
        block for flag, block in blocks.items() if any(getattr(task, flag) for task in tasks)
    )


# 方言ごとの書き方。ARGS の渡し方、PROJECT_DIR、.NOTPARALLEL は make だけ、
# windows-shell、set lists、quote() の説明は just だけの書き方である。
MAKE_DIALECT = """\
# 引数は `make <target> ARGS="..."` の形で渡す。`$(ARGS)` は文字どおりに展開されてから sh に
# 渡るため、空白を含む値は ARGS の中で引用したときに限って 1 引数のまま届く
# （例: `make jigane ARGS="new files src --name x --intent 'a b'"`）。
ARGS =

# 外で有効にした仮想環境を uv へ渡さない。渡すと、uv が自分のプロジェクトの環境と食い違う
# 警告を呼び出しのたびに出す。uv の --active は外の環境へ入れてしまうので使わない。
# PYTHONPATH も渡さない。空の要素があるとカレントディレクトリが import の道に入り、
# --project とは別の jigane を読むため。
unexport VIRTUAL_ENV
unexport PYTHONPATH

# この Makefile の置き場。別の木から `make -f <jigane>/Makefile jigane` で呼んでも、
# jigane 自身の木をプロジェクトにするために使う。
# 空白を含むパスでも語に分かれないよう、dir や realpath（空白で語に分ける）ではなくシェルで求める。
# ここは include より前なので、MAKEFILE_LIST の中身はこの Makefile だけである。
PROJECT_DIR := $(shell cd "$$(dirname "$(strip $(MAKEFILE_LIST))")" && pwd -P)/

# 依存の順に 1 つずつ走らせる（check は lint のあとに test）。
.NOTPARALLEL:
"""

JUST_DIALECT = """\
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# 本文の行は既定で画面に出さない。make は本文の行頭の @ で同じ表示にしている。
# set quiet の下では行頭の @ を付けても表示には切り替わらない（just 1.58 で確認）。
# entrypoints.toml で show_commands を真にした作業は、走らせる前に各コマンドの行を出すため、
# レシピに [no-quiet] を付けている。
set quiet

# 可変長引数 `*args` は既定では 1 本の空白区切り文字列に結合されてから展開されるため、
# `--intent "a b"` のように空白を含む値を渡すと `a` と `b` に分かれてしまう。`quote()` は
# 要素ごとに単一引用符で包んで渡し直すが、要素単位で効かせるには可変長引数を「文字列の
# リスト」として保持する必要があり、これは `set lists`（`set unstable` の配下）で有効になる:
# https://github.com/casey/just/blob/master/GRAMMAR.md
# `quote()` は just 自身が展開する文字列操作なので、sh・PowerShell のどちらが本文を実行しても
# 単一引用符で囲われた 1 引数として渡る。
#
# FIXME(just-quote-powershell): `quote()` は単一引用符を sh の規則（`'\\''`）でエスケープする。
# PowerShell の規則（`''`）とは異なるため、値に単一引用符を含む引数は Windows で壊れうる。
# 直すときは、値に単一引用符を含む引数（例: `--reason "a's b"`）を Windows で渡し、
# 元の文字列がそのまま届くことを確かめる。
set unstable
set lists

# 外で有効にした仮想環境を uv へ渡さない。渡すと、uv が自分のプロジェクトの環境と食い違う
# 警告を呼び出しのたびに出す。uv の --active は外の環境へ入れてしまうので使わない。
# PYTHONPATH も渡さない。空の要素があるとカレントディレクトリが import の道に入り、
# --project とは別の jigane を読むため。
unexport VIRTUAL_ENV
unexport PYTHONPATH

# 引数を渡さず `just` とだけ打ったときは、レシピの一覧を出す。
default:
    just --list
"""


@dataclass(frozen=True)
class Tool:
    """setup の前に確かめる外の道具。verified が空なら、有無だけを見る。

    known_broken 以下の版は止め、known_broken より新しく verified より古い版は警告して続ける。
    """

    name: str
    known_broken: str
    verified: str
    update: str


VERSION = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class CloneGuide:
    """git の作業木でない木に出す、取り出し直しの案内に要る事実。

    案内を出さない木は、この型の値を持たない（Facts.clone_guide が None）。
    """

    url: str
    # --recurse-submodules が要るとき、submodule として持つ木の名前。要らなければ None
    submodule: str | None


@dataclass(frozen=True)
class Facts:
    """二つの方言に共通する事実。"""

    summary: str
    # 生成物を作り直す作業の名前。生成物の先頭には、方言ごとの呼び方で書く
    regenerate_task: str
    uv_missing_message: str
    uv_install_message: str
    uv_install_url: str
    # この木の下に置いた別の木の、この木からの相対の名前。無ければ空で、subproject の置換は使えない
    subproject: str = ""
    # subproject の環境ができたことの目印になる実行ファイルの名前（.venv の bin か Scripts の下）
    subproject_executable: str = ""
    # 切り出しの前の木に残りうる、根の直下の旧い置き場。あれば消す手順を知らせる
    legacy_path: str = ""
    # 作業木でない木での取り出し直しの案内。clone_url と recurse_submodules から作る
    clone_guide: CloneGuide | None = None
    tools: tuple[Tool, ...] = ()

    def notice(self, tool: str) -> str:
        return (
            f"生成物なので直接直さない。entrypoints.toml を直し、"
            f"`{tool} {self.regenerate_task}` で生成する。"
        )


@dataclass(frozen=True)
class Task:
    name: str
    description: str
    commands: tuple[str, ...]
    depends: tuple[str, ...]
    # 先頭で、根が git の作業木かを確かめる。git の作業木を前提にする作業が付ける
    require_git_worktree: bool = False
    # 先頭で、[[tool]] の外の道具の有無と最低の版を確かめる
    require_tools: bool = False
    # 先頭で、根に旧い置き場の残りがあれば消す手順を知らせる（止めない）
    warn_legacy_path: bool = False
    # 先頭で、subproject の環境ができているかを確かめる。
    # できていなければ setup の案内を出して止まる
    require_subproject_env: bool = False
    # 本文の各コマンドを、走らせる前に出力へ出す。黙って落ちたコマンドの名を出力に残すため
    show_commands: bool = False
    # Makefile（macOS・Linux）にだけ、commands の後に続けて走らせるコマンド。
    # justfile（Windows）には出さない。POSIX の仕組みに頼る試験の置き場を走らせる
    posix_commands: tuple[str, ...] = ()

    @property
    def uses_args(self) -> bool:
        return any(f"{{{Placeholder.ARGS.value}}}" in command for command in self.commands)


@dataclass(frozen=True)
class Definition:
    facts: Facts
    tasks: tuple[Task, ...]


def _string_list(table: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}: {key} は文字列の配列でなければならない")
    return tuple(value)


def _parse_facts(table: dict[str, Any]) -> Facts:
    clone_keys = {"clone_url", "recurse_submodules"}
    fields = (set(Facts.__dataclass_fields__) - {"tools", "clone_guide"}) | clone_keys
    optional = {"subproject", "subproject_executable", "legacy_path", "clone_url"}
    optional_flags = {"recurse_submodules"}
    required = fields - optional - optional_flags
    if not required <= set(table) <= fields:
        raise ValueError(
            f"[entrypoints]: 鍵は {sorted(required)} の全てが要る"
            f"（不足 {sorted(required - set(table))}、未知 {sorted(set(table) - fields)}）"
        )
    for key, value in table.items():
        if key in optional_flags:
            if not isinstance(value, bool):
                raise ValueError(f"[entrypoints]: {key} は真偽値でなければならない")
        elif not isinstance(value, str):
            raise ValueError(f"[entrypoints]: {key} は文字列でなければならない")
    facts = Facts(**{key: value for key, value in table.items() if key not in clone_keys})
    if "subproject" in table and not SUBPROJECT_NAME.match(facts.subproject):
        raise ValueError(
            f"[entrypoints]: subproject は木の下の一つの名前にする: {facts.subproject!r}"
        )
    return replace(facts, clone_guide=_parse_clone_guide(table, facts.subproject))


def _parse_clone_guide(table: dict[str, Any], subproject: str) -> CloneGuide | None:
    recurse = table.get("recurse_submodules", False)
    if "clone_url" not in table:
        if recurse:
            raise ValueError("[entrypoints]: recurse_submodules は clone_url が要る")
        return None
    if not recurse:
        return CloneGuide(table["clone_url"], None)
    if not subproject:
        raise ValueError("[entrypoints]: recurse_submodules は subproject が要る")
    return CloneGuide(table["clone_url"], subproject)


GUARD_FLAGS = (
    "require_git_worktree",
    "require_tools",
    "warn_legacy_path",
    "require_subproject_env",
)

# 作業の真偽値の鍵。GUARD_FLAGS は先頭の確かめ、show_commands は本文の行の表示
TASK_FLAGS = (*GUARD_FLAGS, "show_commands")


def _parse_tool(table: dict[str, Any], index: int) -> Tool:
    unknown = set(table) - {"name", "known_broken", "verified", "update"}
    if unknown or not {"name", "update"} <= set(table):
        raise ValueError(
            f"tool[{index}]: 鍵は name と update が要り、known_broken と verified は組で任意"
        )
    for key, value in table.items():
        if not isinstance(value, str):
            raise ValueError(f"tool[{index}]: {key} は文字列でなければならない")  # noqa: TRY004 宣言の誤りは ValueError に揃える
    known_broken = table.get("known_broken", "")
    verified = table.get("verified", "")
    if bool(known_broken) != bool(verified):
        raise ValueError(f"tool[{index}]: known_broken と verified は両方を書くか、両方を省く")
    for key, version in (("known_broken", known_broken), ("verified", verified)):
        if version and not VERSION.match(version):
            raise ValueError(f"tool[{index}]: {key} は「数.数.数」の形にする: {version!r}")
    if verified:
        order = [tuple(int(n) for n in v.split(".")) for v in (known_broken, verified)]
        if order[0] >= order[1]:
            raise ValueError(f"tool[{index}]: known_broken は verified より古くする")
    return Tool(table["name"], known_broken, verified, table["update"])


def _posix_commands(
    table: dict[str, Any], name: str, commands: tuple[str, ...]
) -> tuple[str, ...]:
    posix_commands = _string_list(table, "posix_commands", name)
    if posix_commands and not commands:
        raise ValueError(f"{name}: posix_commands は commands を持つ作業にだけ付ける")
    return posix_commands


def _parse_task(table: dict[str, Any], index: int) -> Task:
    name = table.get("name")
    if not isinstance(name, str):
        raise ValueError(f"task[{index}]: name は文字列でなければならない")  # noqa: TRY004 宣言の誤りは ValueError に揃える
    unknown = set(table) - {
        "name",
        "description",
        "commands",
        "depends",
        "posix_commands",
        *TASK_FLAGS,
    }
    if unknown:
        raise ValueError(f"{name}: 未知の鍵 {sorted(unknown)}")
    description = table.get("description")
    if not isinstance(description, str):
        raise ValueError(f"{name}: description は文字列でなければならない")  # noqa: TRY004 宣言の誤りは ValueError に揃える
    flags: dict[str, bool] = {}
    for flag in TASK_FLAGS:
        value = table.get(flag, False)
        if not isinstance(value, bool):
            raise ValueError(f"{name}: {flag} は真偽値でなければならない")  # noqa: TRY004 宣言の誤りは ValueError に揃える
        flags[flag] = value
    commands = _string_list(table, "commands", name)
    depends = _string_list(table, "depends", name)
    posix_commands = _posix_commands(table, name, commands)
    if not commands and not depends:
        raise ValueError(f"{name}: commands と depends のどちらかは持つ")
    known = {placeholder.value for placeholder in Placeholder}
    for command in (*commands, *posix_commands):
        unknown_names = set(PLACEHOLDER.findall(command)) - known
        if unknown_names:
            raise ValueError(f"{name}: 未定義の置換 {sorted(unknown_names)}")
    if any(flags.values()) and depends:
        raise ValueError(f"{name}: {sorted(TASK_FLAGS)} は commands を持つ作業にだけ付ける")
    return Task(name, description, commands, depends, posix_commands=posix_commands, **flags)


def _check_task(task: Task, facts: Facts, names: list[str]) -> None:
    """task の付けた印と置換が、[entrypoints] と作業の一覧で満たされているかを確かめる。"""
    subproject_names = {placeholder.value for placeholder in SUBPROJECT_PLACEHOLDERS}
    if task.require_git_worktree and facts.clone_guide is None:
        # 値の無い案内を出すと誤った手順を配るため、案内を黙って省かず生成の時点で止める
        raise ValueError(f"{task.name}: require_git_worktree は clone_url が要る")
    if task.require_tools and not facts.tools:
        raise ValueError(f"{task.name}: require_tools は [[tool]] が要る")
    if task.warn_legacy_path and not facts.legacy_path:
        raise ValueError(f"{task.name}: warn_legacy_path は legacy_path が要る")
    if task.require_subproject_env and not (facts.subproject and facts.subproject_executable):
        raise ValueError(
            f"{task.name}: require_subproject_env は subproject と subproject_executable が要る"
        )
    used = {name for command in task.commands for name in PLACEHOLDER.findall(command)}
    if used & subproject_names and not facts.subproject:
        raise ValueError(f"{task.name}: subproject の置換は [entrypoints] の subproject が要る")
    for dependency in task.depends:
        if dependency not in names:
            raise ValueError(f"{task.name}: 未定義の作業 {dependency} を呼んでいる")


def load(path: Path = DEFINITION) -> Definition:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - {"entrypoints", "task", "tool"}
    if unknown:
        raise ValueError(f"未知の節 {sorted(unknown)}")
    if "entrypoints" not in data:
        raise ValueError("[entrypoints] が無い")
    facts = _parse_facts(data["entrypoints"])
    tools = tuple(_parse_tool(table, index) for index, table in enumerate(data.get("tool", [])))
    facts = replace(facts, tools=tools)
    tasks = tuple(_parse_task(table, index) for index, table in enumerate(data.get("task", [])))
    names = [task.name for task in tasks]
    for name in sorted({name for name in names if names.count(name) > 1}):
        raise ValueError(f"{name}: 作業の名前が重複している")
    for task in tasks:
        _check_task(task, facts, names)
    if facts.regenerate_task not in names:
        raise ValueError(f"[entrypoints]: regenerate_task の {facts.regenerate_task} が作業に無い")
    return Definition(facts, tasks)


def expand(command: str, table: dict[Placeholder, str]) -> str:
    return PLACEHOLDER.sub(lambda match: table[Placeholder(match.group(1))], command)


# 木の道の置換の直後に空白でも引用でもない道の文字が続くとき、その続きまでを道の一部とみなす。
# 続く文字を引用の外に置くと、PowerShell は引用した文字列と続きを別の引数に分けるため。
PATH_CONTINUATION = re.compile(r"\{(project_dir|subproject_dir)\}([A-Za-z0-9._/-]+)")


def expand_just(command: str, subproject: str) -> str:
    """just 向けに展開する。道の置換に続く道は just の式の中で結合し、一つの引用に入れる。"""
    roots = {
        "project_dir": "justfile_directory()",
        "subproject_dir": f'justfile_directory() / "{subproject}"',
    }

    def join(match: re.Match[str]) -> str:
        # 先頭の / を残すと just の / 演算子は右辺を絶対パスとして左辺を捨てる
        rest = match.group(2).lstrip("/")
        return f'{{{{quote({roots[match.group(1)]} / "{rest}")}}}}' if rest else match.group(0)

    return expand(PATH_CONTINUATION.sub(join, command), just_placeholders(subproject))


def render_makefile(definition: Definition) -> str:
    facts, tasks = definition.facts, definition.tasks
    names = " ".join(sorted(["help", *(task.name for task in tasks)]))
    lines = [
        f"# {facts.summary}（macOS・Linux 向け）。Windows は justfile を使う。",
        f"# {facts.notice('make')}",
        "",
        MAKE_DIALECT,
        "# uv が無ければ、導入の案内を出して止まる。uv の導入はしない。",
        f"UV_INSTALL_MESSAGE = {facts.uv_install_message}",
        f"UV_INSTALL_URL = {facts.uv_install_url}",
        "define require_uv",
        "@command -v uv >/dev/null 2>&1 || { \\",
        (
            f'\techo "  x {facts.uv_missing_message}。'
            '$(UV_INSTALL_MESSAGE): $(UV_INSTALL_URL)" >&2;'
            " exit 1; }"
        ),
        "endef",
        "",
        *make_guard_defines(facts, tasks),
        f".PHONY: {names}",
        "",
        "# 引数を渡さず `make` とだけ打ったときは、使い方と作業の一覧を出す。",
        ".DEFAULT_GOAL := help",
        "help:",
        '\t@echo "使い方: make <target> [ARGS=\\"...\\"]"',
        '\t@echo ""',
    ]
    width = max(len(task.name) for task in tasks) + 2
    lines.extend(f'\t@echo "  {task.name:<{width}}{task.description}"' for task in tasks)
    for task in tasks:
        lines.append("")
        lines.append(f"# {task.description}。")
        # 依存の後に commands を続ける形では、並列の make でも依存が済んでから commands が走る
        lines.append(f"{task.name}: {' '.join(task.depends)}" if task.depends else f"{task.name}:")
        if not task.commands:
            continue
        lines.append("\t$(call require_uv)")
        lines.extend(f"\t$(call {flag})" for flag in GUARD_FLAGS if getattr(task, flag))
        lines.extend(
            f"\t{'' if task.show_commands else '@'}"
            f"{expand(command, make_placeholders(facts.subproject))}"
            for command in (*task.commands, *task.posix_commands)
        )
    return "\n".join(lines) + "\n"


def render_justfile(definition: Definition) -> str:
    facts, tasks = definition.facts, definition.tasks
    lines = [
        f"# {facts.summary}。macOS・Linux の make と同じ作業を持ち、Windows ではこちらを使う。",
        f"# {facts.notice('just')}",
        f"# uv が要る。無ければ、{facts.uv_install_message}: {facts.uv_install_url}",
        "",
        JUST_DIALECT,
    ]
    guards = just_guard_recipes(facts, tasks)
    if guards:
        lines.append(guards)
    for task in tasks:
        lines.append(f"# {task.description}。")
        signature = " *args" if task.uses_args else ""
        depends = (
            tuple(JUST_GUARD_TASKS[flag] for flag in GUARD_FLAGS if getattr(task, flag))
            or task.depends
        )
        dependencies = " " + " ".join(depends) if depends else ""
        if task.show_commands:
            lines.append("[no-quiet]")
        lines.append(f"{task.name}{signature}:{dependencies}")
        lines.extend(f"    {expand_just(command, facts.subproject)}" for command in task.commands)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Makefile と justfile を作る")
    # 既定はこの生成器を持つ木。サブモジュールとして取り込んだ側の木の入口も、この生成器で作る。
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="entrypoints.toml と生成物の置き場"
    )
    root: Path = parser.parse_args(argv).root
    definition = load(root / "entrypoints.toml")
    (root / "Makefile").write_text(render_makefile(definition), encoding="utf-8")
    (root / "justfile").write_text(render_justfile(definition), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
