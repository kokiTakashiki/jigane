"""MCP の選択。上流 CLI に渡す名前と設定または実行ファイルのパスを持つ。"""

import logging
import os
import re
import shlex
import stat
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from jigane.llm_runs.claude import MCP_RESOURCE_TOOLS
from jigane.llm_runs.codex_mcp import StdioServer
from jigane.storage.store import data_home, require_private_data_home

LOGGER = logging.getLogger(__name__)
MCP_CONFIG_DIRECTORY = "mcp-claude"
CODEX_MCP_DIRECTORY = "mcp-codex"
MCP_CONFIG_FILE = "mcp.json"  # 移行元の名前
MCP_HOME_ENVIRONMENT = "JIGANE_MCP_HOME"
_MCP_NAME = re.compile(r"[A-Za-z0-9_-]+")
ALL_TOOLS_NOTICE = "Claude Code はこれらのサーバの道具をすべて承認なしで使います"


class SecurityConfigError(Exception):
    pass


def _registration_home() -> Path:
    override = os.environ.get(MCP_HOME_ENVIRONMENT)
    home = data_home() if override is None else Path(override)
    if override is not None and (not home.is_absolute() or not home.is_dir()):
        raise SecurityConfigError(
            f"{MCP_HOME_ENVIRONMENT} に既存のディレクトリの絶対パスを指定してください"
        )
    home = home.resolve()
    require_private_data_home(home)
    return home


def _checked_path(path: Path, *, directory: bool = False, codex_cli: bool = False) -> None:
    """CLI に渡す設定を lstat だけで確認する。本文は Claude Code が開く。"""
    migration = (
        "信頼できる stdio 実行ファイルを mcp-codex/<名前> として登録してください"
        if codex_cli
        else f"旧 {MCP_CONFIG_FILE} の登録を mcp-claude/<名前>.json にサーバごとに移してください"
    )
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise SecurityConfigError(f"MCP の設定がありません: {path}。{migration}") from None
    except OSError:
        raise SecurityConfigError(f"MCP の設定の状態を確認できません: {path}") from None
    mode_matches = stat.S_ISDIR if directory else stat.S_ISREG
    if not mode_matches(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        kind = "ディレクトリ" if directory else "ファイル"
        raise SecurityConfigError(f"MCP の設定には通常の{kind}を使ってください: {path}")
    if sys.platform == "win32":  # noqa: TID251 POSIX 所有者 API の無い Windows では ACL に任せる
        return
    if info.st_uid != os.getuid():
        raise SecurityConfigError(f"MCP の設定の所有者を実行ユーザーにしてください: {path}")
    mode = stat.S_IMODE(info.st_mode)
    chmod = f"chmod {'700' if directory else '600'} {shlex.quote(str(path))}"
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SecurityConfigError(
            f"{path} はグループか他の利用者が書き込めます。"
            f"設定を確認し、`{chmod}` で権限を絞ってください"
        )
    if mode & 0o077 and not directory:
        LOGGER.warning("%s の権限を絞ってください（%s）", path, chmod)


def _codex_executable(registration: Path, name: str) -> Path:
    """内容を開かず、利用者が登録した実行ファイルの実体を解く。"""
    error = SecurityConfigError(
        f"MCP サーバ {name} の実行ファイルを確認できません。"
        f"{CODEX_MCP_DIRECTORY}/{name} に、信頼できる実行ファイルへのリンクを置いてください"
    )
    if sys.platform == "win32":  # noqa: TID251 Codex のこの経路は POSIX の権限を使う
        raise error
    try:
        executable = registration.resolve(strict=True)
        info = executable.stat()
        if not stat.S_ISREG(info.st_mode) or not os.access(executable, os.X_OK):
            raise error
        trusted_owners = (0, os.getuid())
        if info.st_uid not in trusted_owners or info.st_mode & 0o022:
            raise error
        for parent in executable.parents:
            info = parent.stat()
            writable = info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            if info.st_uid not in trusted_owners or writable:
                raise error
    except (OSError, RuntimeError):
        raise error from None
    return executable


def read_guide(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    """--mcp-guide のファイルを読み、形と、名前の集合が --mcps と同じことを確かめる。"""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    # 深い入れ子は tomllib が RecursionError で拒む。TOML として読めないものと同じに扱う
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
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
    name: str
    path: Path
    use_for: str | None


@dataclass(frozen=True)
class McpSelection:
    servers: tuple[McpServer, ...] = ()
    codex_cli: bool = False

    @classmethod
    def none(cls) -> "McpSelection":
        return cls()

    @classmethod
    def load(
        cls, names: tuple[str, ...], guide_path: Path | None = None, *, codex_cli: bool = False
    ) -> "McpSelection":
        if any(not _MCP_NAME.fullmatch(name) for name in names):
            raise SecurityConfigError("MCP の名前は半角英数字、ハイフン、下線で指定してください")
        guide = None if guide_path is None else read_guide(guide_path, names)
        if not names:
            return cls.none()
        home = _registration_home()
        directory = home / (CODEX_MCP_DIRECTORY if codex_cli else MCP_CONFIG_DIRECTORY)
        _checked_path(directory, directory=True, codex_cli=codex_cli)
        servers = tuple(
            McpServer(
                name,
                _codex_executable(directory / name, name)
                if codex_cli
                else directory / f"{name}.json",
                None if guide is None else guide[name],
            )
            for name in names
        )
        if not codex_cli:
            for server in servers:
                _checked_path(server.path)
        return cls(servers, codex_cli)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(server.name for server in self.servers)

    @property
    def config_paths(self) -> tuple[Path, ...]:
        return () if self.codex_cli else tuple(server.path for server in self.servers)

    @property
    def reference_servers(self) -> tuple[StdioServer, ...]:
        if not self.codex_cli:
            return ()
        return tuple(StdioServer(server.name, server.path) for server in self.servers)

    def require_outside(self, working_directory: Path) -> None:
        if not self.servers:
            return
        root = working_directory.resolve()
        if any(server.path.is_relative_to(root) for server in self.servers):
            raise SecurityConfigError(
                "MCP の設定と実行ファイルをレビュー対象の作業ディレクトリの外に置いてください"
            )

    def allowed_tools(self) -> tuple[str, ...]:
        if not self.servers or self.codex_cli:
            return ()
        return (*(f"mcp__{name}" for name in self.names), *MCP_RESOURCE_TOOLS)


NO_MCP = McpSelection.none()
