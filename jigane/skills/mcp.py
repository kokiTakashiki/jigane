"""--mcps で選んだ MCP サーバ。mcp.json で確かめた名前と設定を一つの不変の値にまとめる。"""

import json
import logging
import os
import stat
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jigane.git.process import is_windows
from jigane.llm_runs import claude
from jigane.storage.store import data_home

LOGGER = logging.getLogger(__name__)
MCP_CONFIG_FILE = "mcp.json"
# --allowedTools に mcp__<名前> を渡すと、そのサーバの全ての道具が承認なしで許される
ALL_TOOLS_NOTICE = "これらのサーバの道具はすべて承認なしで使われます"


class SecurityConfigError(Exception):
    pass


def mcp_config_path() -> Path:
    return data_home() / MCP_CONFIG_FILE


def warn_if_broadly_accessible(path: Path) -> None:
    """POSIX で mcp.json にグループか他人の権限があれば警告する。読み込みは止めない。

    設定に認証情報が入りうる。拒まないのは、既存の利用者の起動を権限の指摘で止めないため。
    Windows の権限は mode のビットに現れないので確かめない。
    """
    if is_windows():
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        LOGGER.warning(
            "%s の権限が 0%o です。認証情報が入りうるので 0600 に絞ってください（chmod 600）",
            path,
            mode,
        )


def read_guide(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    """--mcp-guide のファイルを読み、形と、名前の集合が --mcps と同じことを確かめる。"""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SecurityConfigError(
            f"--mcp-guide の {path} を TOML として読めません: {error}"
        ) from error
    if set(document) != {"mcps"} or not isinstance(document["mcps"], dict) or not document["mcps"]:
        raise SecurityConfigError(f"{path} の最上位は [mcps.<名前>] の表だけにしてください")
    entries: dict[str, str] = {}
    for name, table in document["mcps"].items():
        if not isinstance(table, dict) or set(table) != {"use_for"}:
            raise SecurityConfigError(f"{path} の [mcps.{name}] は use_for の鍵だけにしてください")
        use_for = table["use_for"]
        if (
            not isinstance(use_for, str)
            or not use_for.strip()
            or "\n" in use_for
            or "\r" in use_for
        ):
            raise SecurityConfigError(
                f"{path} の [mcps.{name}] の use_for は空でない一行の文字列にしてください"
            )
        entries[name] = use_for.strip()
    if set(entries) != set(names):
        raise SecurityConfigError(
            f"{path} の MCP の名前（{', '.join(entries)}）が "
            f"--mcps（{', '.join(names) or '指定なし'}）と同じ集合ではありません"
        )
    return entries


@dataclass(frozen=True)
class McpServer:
    """選んだ MCP サーバ一つ。use_for は --mcp-guide を渡さなかったときだけ None。"""

    name: str
    config: Any
    use_for: str | None


@dataclass(frozen=True)
class McpSelection:
    """選んだサーバの並び。空が「MCP 無し」の唯一の表し方。"""

    servers: tuple[McpServer, ...] = ()

    @classmethod
    def none(cls) -> "McpSelection":
        return cls()

    @classmethod
    def load(cls, names: tuple[str, ...], guide_path: Path | None = None) -> "McpSelection":
        """mcp.json から指定した名前の設定だけを取り出す。ファイルか名前が無ければ失敗する。

        guide_path があれば、形と名前の集合を確かめてから使い分けを持たせる。
        """
        guide = None if guide_path is None else read_guide(guide_path, names)
        if not names:
            return cls.none()
        path = mcp_config_path()
        if not path.is_file():
            raise SecurityConfigError(
                f"--mcps {' '.join(names)} を指定しましたが {path} がありません。"
                '{"mcpServers": {...}} の形で起動の設定を書いてください'
            )
        warn_if_broadly_accessible(path)
        try:
            available = json.loads(path.read_text(encoding="utf-8")).get("mcpServers")
        except (json.JSONDecodeError, AttributeError) as error:
            raise SecurityConfigError(
                f"{path} が mcpServers を持つ JSON の形ではありません"
            ) from error
        if not isinstance(available, dict):
            raise SecurityConfigError(f"{path} に mcpServers がありません")
        missing = [name for name in names if name not in available]
        if missing:
            raise SecurityConfigError(
                f"{path} に MCP サーバ {', '.join(missing)} の設定がありません"
            )
        # guide を読んだときは read_guide が names と同じ集合を保証するので、全てに use_for が入る
        return cls(
            tuple(
                McpServer(name, available[name], None if guide is None else guide[name])
                for name in names
            )
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(server.name for server in self.servers)

    def allowed_tools(self) -> tuple[str, ...]:
        if not self.servers:
            return ()
        return (*(f"mcp__{name}" for name in self.names), *claude.MCP_RESOURCE_TOOLS)

    def config_content(self) -> dict[str, Any]:
        return {"mcpServers": {server.name: server.config for server in self.servers}}

    @contextmanager
    def config_file(self) -> Iterator[Path | None]:
        """選んだサーバだけの一時の設定ファイルを権限 0600（POSIX）で作り、抜けるときに消す。"""
        if not self.servers:
            # 既定は MCP を使わない。--mcp-config を渡さないので、ファイルを作らない
            yield None
            return
        descriptor, name = tempfile.mkstemp(prefix="jigane-mcp-", suffix=".json")
        path = Path(name)
        try:
            # mkstemp は 0600 で作る。設定に認証情報が入りうるので、他の利用者に読ませない。
            # 0600 は POSIX でしか効かない。
            # Windows では利用者ごとの一時のディレクトリの既定の ACL に頼る
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(self.config_content(), file)
            yield path
        finally:
            path.unlink(missing_ok=True)


NO_MCP = McpSelection.none()
