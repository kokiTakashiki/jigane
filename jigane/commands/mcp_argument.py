"""`--mcps` の引数の定義と検証。`run` と `estimate` が同じものを使う。"""

import argparse
from collections.abc import Iterable
from pathlib import Path

from jigane.groups.definitions import GroupId
from jigane.skills.mcp import McpSelection


def add_mcps_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mcps",
        nargs="+",
        default=(),
        metavar="NAME",
        help=(
            "セキュリティの群だけが使う MCP サーバの名前です（データの置き場の mcp.json の "
            "mcpServers のキー）。省略すると MCP を使いません"
        ),
    )
    parser.add_argument(
        "--mcp-guide",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "セキュリティの群だけが使う、MCP の使い分けを書いた TOML のファイルです。"
            "[mcps.<名前>] の表に use_for を書き、名前の集合は --mcps と同じにします。"
            "省略すると使い分けを渡しません"
        ),
    )


def load_mcp_selection(
    mcp_names: Iterable[str], group: GroupId, guide_path: Path | None = None
) -> McpSelection:
    mcps = tuple(dict.fromkeys(mcp_names))
    if mcps and group != GroupId.SECURITY:
        # 起動の前に誤りにする。黙って捨てると、使っていない MCP を使ったつもりになる
        raise ValueError(f"--mcps はセキュリティの群だけが受け付けます（指定した群: {group}）")
    if guide_path is not None and group != GroupId.SECURITY:
        raise ValueError(
            f"--mcp-guide はセキュリティの群だけが受け付けます（指定した群: {group}）"
        )
    # mcp.json の欠落と名前の誤りは、同意を求める前に止める
    return McpSelection.load(mcps, guide_path)
