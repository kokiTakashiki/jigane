"""生成器（generate_*.py）が共有する、印の間の置き換えと読み書き、layers 契約の取り出し。

標準ライブラリだけで動く。
"""

import re
import sys
import tomllib
from pathlib import Path


def read_exact(path: Path) -> str:
    # newline="" で元の改行を保つ（既定の読み書きは Windows でファイル全体を CRLF にする）
    with path.open(encoding="utf-8", newline="") as source:
        return source.read()


def write_exact(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        destination.write(text)


def layers_lines(pyproject: str) -> list[str]:
    """layers 契約の層の行を、上の層から順に返す。

    契約がちょうど一つでなければ ValueError にする。
    """
    contracts = tomllib.loads(pyproject)["tool"]["importlinter"]["contracts"]
    layered = [contract for contract in contracts if contract["type"] == "layers"]
    if len(layered) != 1:
        raise ValueError("layers 契約がちょうど一つ要る")
    lines: list[str] = layered[0]["layers"]
    return lines


def replace_region(text: str, begin: str, end: str, replacement: str, where: str) -> str:
    """begin と end の組の間を replacement に置き換える。

    組がちょうど一つでなければ ValueError にする。
    """
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    if len(pattern.findall(text)) != 1:
        raise ValueError(f"{where} に印（{begin}）の開始と終了の組がちょうど一つ要る")
    return pattern.sub(lambda _: replacement, text)


def check_or_write(path: Path, expected: str, *, check: bool, guidance: str) -> int:
    """--check なら最新かだけを見て、そうでなければ書く。終了コードを返す。"""
    if check:
        if read_exact(path) != expected:
            sys.stderr.write(f"{path.name} が古い。{guidance}\n")
            return 1
        return 0
    write_exact(path, expected)
    return 0


def fail(error: ValueError) -> int:
    """生成器が入力を拒んだことを一行で伝え、終了コード 1 を返す。"""
    sys.stderr.write(f"{error}\n")
    return 1
