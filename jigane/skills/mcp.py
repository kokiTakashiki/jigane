"""MCP の選択。上流 CLI に渡す名前と設定または実行ファイルのパスを持つ。"""

import os
import re
import tomllib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from jigane.git.tree_files import read_input_text
from jigane.llm_runs import adapter
from jigane.storage.store import data_home, require_private_data_home

CODEX_MCP_DIRECTORY = adapter.CODEX_MCP_DIRECTORY
MCP_CONFIG_DIRECTORY = adapter.MCP_CONFIG_DIRECTORY
MCP_CONFIG_FILE = adapter.MCP_CONFIG_FILE
SecurityConfigError = adapter.SecurityConfigError
_codex_executable = adapter.resolve_stdio_registration  # 既存のパリティ試験の差し替え口
MCP_HOME_ENVIRONMENT = "JIGANE_MCP_HOME"
_MCP_NAME = re.compile(r"[A-Za-z0-9_-]+")
ALL_TOOLS_NOTICE = "Claude Code はこれらのサーバの道具をすべて承認なしで使います"


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


def read_guide(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    """--mcp-guide のファイルを読み、形と、名前の集合が --mcps と同じことを確かめる。"""
    try:
        document = tomllib.loads(read_input_text(Path.cwd(), path))
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
        agent = adapter.adapter_for_mcp(codex_cli=codex_cli)
        paths = agent.bind_mcp(names, _registration_home(), _codex_executable)
        servers = tuple(
            McpServer(name, path, None if guide is None else guide[name])
            for name, path in zip(names, paths, strict=True)
        )
        return cls(servers, codex_cli)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(server.name for server in self.servers)

    @cached_property
    def _binding(self) -> adapter.McpBinding:
        agent = adapter.adapter_for_mcp(codex_cli=self.codex_cli)
        return agent.mcp_launch_options(self.names, tuple(server.path for server in self.servers))

    @property
    def config_paths(self) -> tuple[Path, ...]:
        return self._binding.config_paths

    @property
    def reference_servers(self) -> tuple[adapter.McpReferenceServer, ...]:
        return self._binding.reference_servers

    def require_outside(self, working_directory: Path) -> None:
        if not self.servers:
            return
        root = working_directory.resolve()
        if any(server.path.is_relative_to(root) for server in self.servers):
            raise SecurityConfigError(
                "MCP の設定と実行ファイルをレビュー対象の作業ディレクトリの外に置いてください"
            )

    def allowed_tools(self) -> tuple[str, ...]:
        return self._binding.allowed_tools


NO_MCP = McpSelection.none()
