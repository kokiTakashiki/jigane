"""引数の群を、入口で一度だけ `GroupId` に解く。

文字列のまま運ぶと、各所で `GroupId(...)` に直す変換が散る。群を取る副コマンドはここを通す。
"""

import argparse

from jigane.groups.definitions import GroupId


def parse_group(text: str) -> GroupId:
    try:
        return GroupId(text)
    except ValueError:
        # 型の変換が選択肢の検査より先に走るため、argparse 既定の「選択肢から選べ」の文をここで作る
        raise argparse.ArgumentTypeError(
            f"invalid choice: {text!r} (choose from {', '.join(GroupId)})"
        ) from None


def add_group_argument(parser: argparse.ArgumentParser, name: str = "group") -> None:
    """群を取る位置引数を足す。選択肢は `GroupId` の値から作る。"""
    parser.add_argument(name, type=parse_group, choices=tuple(GroupId))
