"""設計書の名指しを引き当てるための、ファイル直下の文字列定数の見方。

宣言の読み取りは言語のアダプタ（`base.StringConstantReader`）が行い、ここは
読んだ定数から、副コマンドの名前と保存物のファイル名に関わるものを選ぶ。
"""

import re
from collections.abc import Iterable

from jigane.languages.base import StringConstant
from jigane.storage.store_suffixes import STORED_FILE_SUFFIXES


def names_holding_stored_file(constants: Iterable[StringConstant], name: str) -> set[str]:
    """値が保存物 `name`（`dir/name` も含む）である定数の名前。"""
    pattern = re.compile(rf"""(?:[^"'\s]*/)?{re.escape(name)}""")
    return {c.name for c in constants if pattern.fullmatch(c.value)}


def stored_file_names(constants: Iterable[StringConstant]) -> set[str]:
    """値が保存物のファイル名（ディレクトリを除く）である定数の値。返すのは定数の名前ではなく、ファイル名。"""
    names: set[str] = set()
    for constant in constants:
        name = constant.value.rsplit("/", 1)[-1]
        if name.rsplit(".", 1)[-1] in STORED_FILE_SUFFIXES:
            names.add(name)
    return names
