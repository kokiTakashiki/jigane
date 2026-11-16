"""方針のレビューに渡す、副コマンドの引数の表。argparse の `add_argument` を構文木だけから読む。"""

from pathlib import Path

from jigane.figures.models import TableFigure
from jigane.git.tree_files import read_tree_bytes
from jigane.languages import registry
from jigane.languages.base import CommandArgument, CommandArgumentExtractor, parse
from jigane.reviews.lifecycle import Review
from jigane.skills import policy_tables

SECTION_COMMANDS = "副コマンドの引数"
INTRO = (
    "変更したファイルが定める副コマンドの引数を、ソースの原文のまま並べた。値は評価していない。"
    "引数の欄の `+` は変更で追加、`-` は削除、`~` は既定値か必須の変更。"
    "括弧の群は `add_argument_group`、排他の群は `add_mutually_exclusive_group` の変数の名前。"
)
HEADER = ("ファイル", "副コマンド", "引数", "dest", "既定値", "必須", "action", "type", "choices")
# ファイル、副コマンド、先頭の名前の三つ組
type _Key = tuple[str, str, str]


def _arguments_of(root: Path, path: str) -> list[CommandArgument]:
    adapter = registry.adapter_for(path)
    data = read_tree_bytes(root, path)
    if not isinstance(adapter, CommandArgumentExtractor) or data is None:
        return []
    return adapter.command_arguments(parse(adapter, data).root_node, data, path)


def _key(argument: CommandArgument) -> _Key:
    return (
        argument.path,
        argument.subcommand_name,
        argument.option_names[0] if argument.option_names else "",
    )


def _row(argument: CommandArgument, mark: str) -> tuple[str, ...]:
    names = mark + ", ".join(argument.option_names)
    if argument.group_variable_name is not None:
        names += (
            f"（{'排他' if argument.group_exclusive else '群'} {argument.group_variable_name}）"
        )
    return (
        argument.path,
        argument.subcommand_name or "（最上位）",
        names,
        argument.dest_text or "",
        argument.default_text or "",
        "はい" if argument.required else "",
        argument.action_text or "",
        argument.type_text or "",
        argument.choices_text or "",
    )


def command_table(review: Review) -> TableFigure | None:
    """副コマンドの引数の表。引数が一つも無ければ None。"""
    return policy_tables.diff_table(
        review,
        extract=_arguments_of,
        key=_key,
        changed=lambda old, new: (
            (old.default_text, old.required) != (new.default_text, new.required)
        ),
        row=_row,
        sort_key=lambda a: (a.path, a.subcommand_name, a.line),
        heading=policy_tables.TableHeading(INTRO, HEADER),
    )
