"""docs/commands.md の「コマンド一覧」にある副コマンドの一覧表を作る。

    uv run --locked python scripts/generate_command_table.py

表は docs/commands.md の開始と終了の印の間に書く。段階の割り当てはここの STAGES が持ち、
役割の文は build_parser の各副コマンドの help をそのまま使う。
"""

import argparse
import ast
import re
import sys
from pathlib import Path

from jigane.commands import build_parser

ROOT = Path(__file__).resolve().parent.parent
DESTINATION = Path("docs/commands.md")

BEGIN = "<!-- BEGIN GENERATED: command-table (scripts/generate_command_table.py) -->"
END = "<!-- END GENERATED: command-table -->"
REGION = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.DOTALL)

# 利用者がレビューを進める順に並べる。副コマンドを足したら、ここの一つの段階へ入れる
STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("レビューを作る", ("new", "refresh", "list", "archive", "unarchive", "delete")),
    (
        "見る",
        ("show", "diagrams", "sequence", "technology-list", "design", "estimate", "usage"),
    ),
    ("解析する", ("run", "flaky")),
    ("指摘を選ぶ", ("pick", "unpick", "hold", "unhold", "object", "unobject")),
    ("書き出す", ("export-reply", "export-report", "templates")),
    ("導入", ("select-agent", "languages")),
    ("確かめる", ("debug",)),
)


def subcommand_module(name: str, root: Path = ROOT) -> Path:
    return root / "jigane" / "commands" / "subcommands" / f"{name.replace('-', '_')}.py"


def references_attribute(name: str, attribute: str) -> bool:
    """副コマンドのモジュールが、属性 attribute を参照しているか。"""
    tree = ast.parse(subcommand_module(name).read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Attribute) and node.attr == attribute for node in ast.walk(tree)
    )


def calls_function(name: str, function: str) -> bool:
    """副コマンドのモジュールが、名前 function の関数を呼んでいるか。import だけでは数えない。"""
    tree = ast.parse(subcommand_module(name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            match node.func:
                case ast.Name(id=called) | ast.Attribute(attr=called) if called == function:
                    return True
    return False


def launches_model(name: str) -> bool:
    """直接の起動口を参照するコマンドと、共有の群の実行を呼ぶコマンドを数える。"""
    return references_attribute(name, "invoke") or calls_function(name, "run_group")


def subcommand_helps() -> dict[str, str]:
    """副コマンド名から help を引く表。"""
    parser = build_parser()
    for action in parser._actions:  # noqa: SLF001 argparse は副コマンドの一覧を公開の口で返さない
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001 副コマンドの action を型で選り分ける公開の手段が無い
            return {choice.dest: choice.help or "" for choice in action._choices_actions}  # noqa: SLF001 副コマンドごとの help を引く公開の口が無い
    raise ValueError("build_parser に副コマンドが無い")


def render_table(helps: dict[str, str]) -> str:
    """印を含む表の全文を返す。段階に入らない副コマンドや重複は ValueError にする。"""
    assigned = [name for _, names in STAGES for name in names]
    duplicated = sorted({name for name in assigned if assigned.count(name) > 1})
    if duplicated:
        raise ValueError(f"複数の段階に入っている副コマンド: {', '.join(duplicated)}")
    unassigned = sorted(set(helps) - set(assigned))
    if unassigned:
        raise ValueError(f"段階に入っていない副コマンド: {', '.join(unassigned)}")
    unknown = sorted(set(assigned) - set(helps))
    if unknown:
        raise ValueError(f"存在しない副コマンドが段階にある: {', '.join(unknown)}")
    lines = [
        BEGIN,
        "",
        "| 段階 | コマンド | モデルを呼び出す | 役割 |",
        "| --- | --- | --- | --- |",
    ]
    for stage, names in STAGES:
        for name in names:
            launches = "はい" if launches_model(name) else "いいえ"
            lines.append(f"| {stage} | `{name}` | {launches} | {helps[name]} |")
    return "\n".join([*lines, "", END])


def replace_region(document: str, table: str) -> str:
    if len(REGION.findall(document)) != 1:
        raise ValueError(f"{DESTINATION.as_posix()} に表の印（開始と終了の組）がちょうど一つ要る")
    return REGION.sub(lambda _: table, document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"{DESTINATION.as_posix()} の副コマンドの一覧表を作る"
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="リポジトリの根")
    destination = parser.parse_args(argv).root / DESTINATION
    destination.write_text(
        replace_region(destination.read_text(encoding="utf-8"), render_table(subcommand_helps())),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
