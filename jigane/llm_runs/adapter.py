"""レビューエージェントの起動口を選ぶ。"""

import logging
import os
import shlex
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from jigane.groups.definitions import GroupId
from jigane.groups.policy_runs import PolicyRole
from jigane.llm_runs import claude, claude_mcp, codex, codex_mcp
from jigane.llm_runs.invocation import Invocation, InvocationResult, Invoke

MCP_CONFIG_DIRECTORY = "mcp-claude"
MCP_CONFIG_FILE = "mcp.json"  # 移行元の名前
CODEX_MCP_DIRECTORY = "mcp-codex"
_CLAUDE_MIGRATION = (
    f"旧 {MCP_CONFIG_FILE} の登録を "
    f"{MCP_CONFIG_DIRECTORY}/<名前>.json にサーバごとに移してください"
)
_CODEX_MIGRATION = "信頼できる stdio 実行ファイルを mcp-codex/<名前> として登録してください"
LOGGER = logging.getLogger("jigane.skills.mcp")  # 既存の権限警告の出どころを保つ
McpReferenceServer = codex_mcp.StdioServer
ResolveExecutable = Callable[[Path, str], Path]


class SecurityConfigError(Exception):
    pass


@dataclass(frozen=True)
class McpBinding:
    config_paths: tuple[Path, ...] = ()
    reference_servers: tuple[McpReferenceServer, ...] = ()
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Disclosure:
    command: str
    data_flow: str
    isolation: tuple[str, ...] = ()
    mcp_notes: tuple[str, ...] = ()
    model_and_billing: tuple[str, ...] = ()


class AgentAdapter(Protocol):
    def invoke(self, request: Invocation, /) -> InvocationResult: ...
    def disclosure(
        self, group: GroupId, principles: tuple[str, ...], role: PolicyRole | None
    ) -> Disclosure: ...
    def run_settings(
        self, settings: claude.RunSettings, role: PolicyRole | None
    ) -> claude.RunSettings: ...
    def bind_mcp(
        self, names: tuple[str, ...], home: Path, resolve_executable: ResolveExecutable
    ) -> tuple[Path, ...]: ...
    def mcp_launch_options(
        self, names: tuple[str, ...], paths: tuple[Path, ...]
    ) -> McpBinding: ...


@dataclass(frozen=True)
class _LaunchAdapter:
    invoke: Invoke
    codex_cli: bool

    def disclosure(
        self, group: GroupId, principles: tuple[str, ...], role: PolicyRole | None
    ) -> Disclosure:
        if not self.codex_cli:
            return Disclosure(
                command="Claude Code CLI（claude -p）",
                data_flow=(
                    "Claude Code CLI のログインや環境の認証を使い、"
                    "組み立てたレビュー資料と取得した資料を Claude Code の設定先"
                    "（通常は Anthropic）に送ります。"
                ),
            )
        initial = codex.model_for(role, None, discovery_group=group)
        models = (codex.model_for(PolicyRole.PRINCIPLE, number) for number in principles)
        return Disclosure(
            command="Codex CLI（codex exec）",
            data_flow=(
                "Codex CLI に保存された ChatGPT ログインで、OpenAI の ChatGPT（chatgpt.com）に、"
                "jigane が組み立てたコード、文書、履歴、検証資料などを送ります。"
            ),
            isolation=("一時ディレクトリで動かし、シェル、プラグイン、Web 検索を無効にします。",),
            mcp_notes=(
                f"登録したコマンドを起動し、{codex_mcp.REFERENCE_NOTICE}。",
                "取得した資料は同じ送信先へ送ります。",
            ),
            model_and_billing=(
                f"初期起動のモデル {initial.name}（effort {initial.effort}）",
                *(
                    f"原則 {number}: {model.name}（effort {model.effort}）"
                    for number, model in zip(principles, models, strict=True)
                ),
                f"{codex.NO_SPENDING_LIMIT}。",
                "報告された使用量をモデルの公開 API 単価で USD に換算して記録します。",
                "請求額は OpenAI 側で確認してください。",
            ),
        )

    def run_settings(
        self, settings: claude.RunSettings, role: PolicyRole | None
    ) -> claude.RunSettings:
        if not self.codex_cli:
            return settings
        model = codex.model_for(role, None)
        return replace(
            settings,
            budget=codex.review_budget(settings.budget, model.name),
            effort=model.effort,
            has_spending_limit=False,
            allow_tools=False,
        )

    def bind_mcp(
        self, names: tuple[str, ...], home: Path, resolve_executable: ResolveExecutable
    ) -> tuple[Path, ...]:
        if self.codex_cli:
            directory = home / CODEX_MCP_DIRECTORY
            _checked_path(directory, _CODEX_MIGRATION, directory=True)
            return tuple(resolve_executable(directory / name, name) for name in names)
        directory = home / MCP_CONFIG_DIRECTORY
        _checked_path(directory, _CLAUDE_MIGRATION, directory=True)
        paths = tuple(directory / f"{name}.json" for name in names)
        for path in paths:
            _checked_path(path, _CLAUDE_MIGRATION)
        return paths

    def mcp_launch_options(self, names: tuple[str, ...], paths: tuple[Path, ...]) -> McpBinding:
        if self.codex_cli:
            servers = tuple(
                McpReferenceServer(name, path) for name, path in zip(names, paths, strict=True)
            )
            return McpBinding(reference_servers=servers)
        return McpBinding(config_paths=paths, allowed_tools=claude_mcp.allowed_tools(names))


def adapter_for(agent: str | None, *, invoke_override: Invoke | None = None) -> AgentAdapter:
    """保存済みの選択を起動口に変える。未選択時は Claude を使う。"""
    invoke: Invoke
    if agent is None or agent == "claude":
        invoke = claude.run
    elif agent == "codex":
        invoke = codex.run
    else:
        raise ValueError(f"未対応のレビューエージェントです: {agent}")
    return _LaunchAdapter(invoke if invoke_override is None else invoke_override, agent == "codex")


def adapter_for_mcp(*, codex_cli: bool) -> AgentAdapter:
    """既存の MCP 選択フラグからアダプターを得る。"""
    return adapter_for("codex" if codex_cli else None)


def _checked_path(path: Path, migration: str, *, directory: bool = False) -> None:
    """登録ディレクトリとネイティブ設定の属性を、内容を開かず確認する。"""
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


def resolve_stdio_registration(registration: Path, name: str) -> Path:
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
