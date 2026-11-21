"""選択済み Codex MCP の起動設定と CLI が報告した資料の取得。"""

import json
from dataclasses import dataclass
from pathlib import Path

from jigane.text.json_input import loads_bounded

REFERENCE_NOTICE = "Codex CLI が選んだ MCP の資料一覧・テンプレート・本文取得を使います"
STARTUP_FAILURE = "Codex CLI が選択した MCP サーバに接続できませんでした"
READ_FAILURE = "Codex CLI が MCP の資料を取得できませんでした"
_RESOURCE_TOOLS = frozenset(
    {"list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"}
)
_STARTUP_DIAGNOSTIC = (
    "Error: thread/start: thread/start failed: error creating thread: Fatal error: "
    "Failed to initialize session: required MCP servers failed to initialize:"
)


@dataclass(frozen=True)
class StdioServer:
    name: str
    executable: Path


def server_override(servers: tuple[StdioServer, ...]) -> str:
    entries = (
        f"{json.dumps(server.name)}={{"
        f"command={json.dumps(str(server.executable), ensure_ascii=False)},"
        "required=true,enabled_tools=[]}"
        for server in servers
    )
    return "mcp_servers={" + ",".join(entries) + "}"


def _resource_body(item: object) -> bool:
    if (
        not isinstance(item, dict)
        or not isinstance(uri := item.get("uri"), str)
        or not uri.strip()
    ):
        return False
    return any(
        isinstance(value, str) and value.strip() for value in (item.get("text"), item.get("blob"))
    )


def read_server(event: dict[str, object]) -> str | None:
    """Codex CLI が本文の取得を終えたイベントから、サーバ名だけを取る。"""
    if event.get("type") != "item.completed" or not isinstance(item := event.get("item"), dict):
        return None
    if (
        item.get("type") != "mcp_tool_call"
        or item.get("tool") != "read_mcp_resource"
        or item.get("status") != "completed"
        or item.get("error") is not None
        or not isinstance(server := item.get("server"), str)
        or not isinstance(result := item.get("result"), dict)
        or not isinstance(content := result.get("content"), list)
    ):
        return None
    for block in content:
        if (
            not isinstance(block, dict)
            or block.get("type") != "text"
            or not isinstance(body := block.get("text"), str)
        ):
            continue
        try:
            read = loads_bounded(body, 64)
        except ValueError:
            continue
        if (
            isinstance(read, dict)
            and read.get("server") == server
            and read.get("error") is None
            and isinstance(contents := read.get("contents"), list)
            and any(_resource_body(item) for item in contents)
        ):
            return server
    return None


def resource_failure(event: dict[str, object], names: set[str]) -> bool:
    if event.get("type") != "item.completed" or not isinstance(item := event.get("item"), dict):
        return False
    return (
        item.get("type") == "mcp_tool_call"
        and isinstance(tool := item.get("tool"), str)
        and tool in _RESOURCE_TOOLS
        and isinstance(server := item.get("server"), str)
        and server in names
        and item.get("status") == "failed"
    )


def startup_failed(stderr: str) -> bool:
    return any(line.startswith(_STARTUP_DIAGNOSTIC) for line in stderr.split("\n"))
