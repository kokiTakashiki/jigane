"""PR の状態の取得と保存の副コマンドが CLI に残らないことを縛る。

一覧の行から pr_state の欄が消えたことは、test_list_row_effects の ROW_FIELDS の突き合わせが縛る。
"""

import argparse

from jigane.commands import build_parser


def _subcommand_names() -> set[str]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("副コマンドの登録が見つからない")


def test_pr_state_is_not_a_subcommand() -> None:
    assert "pr-state" not in _subcommand_names()
