"""Claude Code が管理する MCP の状態を stream-json のイベントから読む。"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from io import StringIO
from typing import Any

from jigane.text.json_input import JsonTooDeepError, elide_nested_containers, loads_bounded

_READ_RESOURCE = "ReadMcpResourceTool"
MCP_RESOURCE_TOOLS = (_READ_RESOURCE, "ListMcpResourcesTool")

_CONNECTION_FAILURE = "Claude Code が選択した MCP サーバに接続できませんでした"
_AUTH_REQUIRED = "Claude Code が選択した MCP サーバの認証が必要と報告しました"
_DISABLED = "Claude Code が選択した MCP サーバは無効と報告しました"
_TOOL_FAILURE = "Claude Code が MCP の呼び出しに失敗しました"
# tool_use / tool_result の項目は message.content の配列内、外から 4 段目の辞書にある。
_EVENT_METADATA_DEPTH = 4


@dataclass(frozen=True)
class McpOutput:
    result: dict[str, Any] | None
    failure: str | None
    invalid_stream: bool
    read_servers: tuple[str, ...]


def _tool_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return (
        [block for block in content if isinstance(block, dict)]
        if isinstance(content, list)
        else []
    )


def _is_mcp_tool(name: object) -> bool:
    return isinstance(name, str) and (name.startswith("mcp__") or name in MCP_RESOURCE_TOOLS)


def _requested_server(block: dict[str, Any], selected: set[str]) -> str | None:
    if block.get("name") != _READ_RESOURCE or not isinstance(args := block.get("input"), dict):
        return None
    server, uri = args.get("server"), args.get("uri")
    return (
        server
        if isinstance(server, str) and server in selected and isinstance(uri, str) and uri.strip()
        else None
    )


def _resource_body(item: object, server: str) -> bool:
    if (
        not isinstance(item, dict)
        or not isinstance(uri := item.get("uri"), str)
        or not uri.strip()
        or not isinstance(text := item.get("text"), str)
    ):
        return False
    if "blobSavedTo" not in item:
        return bool(text.strip())
    # Claude Code はバイナリを保存した場合、構造化結果に保存先とバイト数の説明を返す。
    # 空のバイナリにも同じ欄があるので、件数やパスの有無だけでは本文取得に数えない。
    path = item["blobSavedTo"]
    if not isinstance(path, str) or not path:
        return False
    prefix = f"[Resource from {server} at {uri}] Binary content ("
    suffix = f" bytes) saved to {path}"
    if not text.startswith(prefix) or not text.endswith(suffix):
        return False
    description = text[len(prefix) : -len(suffix)]
    _, comma, size = description.rpartition(", ")
    return bool(comma and size.isascii() and size.isdecimal() and size.strip("0"))


@dataclass
class _ToolCalls:
    selected: set[str]
    # MCP の成否は全て追い、本文取得の判定では標準の取得道具にだけ選択名を持たせる。
    pending: dict[str, str | None] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    reads: dict[str, str] = field(default_factory=dict)

    def remember(self, event: dict[str, Any]) -> None:
        for block in _tool_blocks(event):
            call_id = block.get("id")
            if block.get("type") != "tool_use" or not isinstance(call_id, str) or not call_id:
                continue
            if call_id in self.seen:
                # 再利用された ID はどちらの呼び出しの結果か確定できない。
                self.reads.pop(call_id, None)
                if call_id in self.pending or _is_mcp_tool(block.get("name")):
                    self.pending[call_id] = None
            elif _is_mcp_tool(block.get("name")):
                self.pending[call_id] = _requested_server(block, self.selected)
            self.seen.add(call_id)

    def resolve(self, event: dict[str, Any]) -> bool:
        blocks = _tool_blocks(event)
        # Claude Code は並列呼び出しでも各結果を別々の user イベントに書く。
        # tool_use_result はイベント全体の欄なので、一つの結果に結び付けられるときだけ使う。
        result = event.get("tool_use_result") if len(blocks) == 1 else None
        failed = False
        for block in blocks:
            call_id = block.get("tool_use_id")
            if block.get("type") != "tool_result" or not isinstance(call_id, str):
                continue
            if call_id not in self.pending:
                self.seen.add(call_id)
                self.reads.pop(call_id, None)
                continue
            server = self.pending.pop(call_id)
            if block.get("is_error") is True:
                failed = True
            elif server is not None and isinstance(result, dict):
                if result.get("error") is not None:
                    failed = True
                elif isinstance(contents := result.get("contents"), list) and any(
                    _resource_body(item, server) for item in contents
                ):
                    self.reads[call_id] = server
        return failed


def _events(stdout: str, max_nesting: int) -> Iterator[dict[str, Any] | None]:
    # splitlines は JSON の文字列内の U+2028 も改行と見なす。
    for line in StringIO(stdout):
        if not line.strip():
            continue
        try:
            event = _load_event(line, max_nesting)
        except ValueError:
            event = None
        yield event if isinstance(event, dict) else None


def _load_event(line: str, max_nesting: int) -> object:
    try:
        return loads_bounded(line, max_nesting, strict=False)
    except JsonTooDeepError:
        # MCP の入力・結果は任意の深さを持ちうる。最終結果以外で必要なのは外側の識別子と状態だけ。
        outer = elide_nested_containers(line, _EVENT_METADATA_DEPTH)
        event = loads_bounded(outer, max_nesting, strict=False)
        if (
            isinstance(event, dict)
            and isinstance(kind := event.get("type"), str)
            and kind != "result"
        ):
            return event
        raise


def _server_statuses(event: dict[str, Any]) -> dict[str, str]:
    servers = event.get("mcp_servers")
    if not isinstance(servers, list):
        return {}
    return {
        name: status
        for server in servers
        if isinstance(server, dict)
        and isinstance(name := server.get("name"), str)
        and isinstance(status := server.get("status"), str)
    }


def _connection_failure(statuses: dict[str, str], selected_names: tuple[str, ...]) -> str | None:
    reported = {statuses.get(name) for name in selected_names}
    failures = {
        "needs-auth": _AUTH_REQUIRED,
        "disabled": _DISABLED,
        "failed": _CONNECTION_FAILURE,
    }
    return next((reason for status, reason in failures.items() if status in reported), None)


def inspect_output(stdout: str, max_nesting: int, selected_names: tuple[str, ...]) -> McpOutput:
    """選択された MCP の接続状態、道具の成否、確認できた本文取得、最終結果を読む。"""
    result = None
    failure = None
    invalid_stream = False
    statuses: dict[str, str] = {}
    calls = _ToolCalls(set(selected_names))
    for event in _events(stdout, max_nesting):
        if event is None:
            invalid_stream = True
            continue
        match event.get("type"):
            case "system" if event.get("subtype") == "init":
                statuses = _server_statuses(event)
                failure = _connection_failure(statuses, selected_names) or failure
            case "assistant" if result is None:
                calls.remember(event)
            case "user" if result is None:
                if calls.resolve(event) and failure is None:
                    failure = _TOOL_FAILURE
            case "result":
                result = event
    # 起動自体が失敗した場合は、init が無くても最終結果の失敗の種類を読める。
    if result is not None and not result.get("is_error"):
        invalid_stream |= statuses.keys() != calls.selected
    reads = set(calls.reads.values()) if not invalid_stream else set()
    read_servers = tuple(name for name in selected_names if name in statuses and name in reads)
    return McpOutput(result, failure, invalid_stream, read_servers)


def reads_before_timeout(
    stdout: bytes | str | None, max_nesting: int, selected_names: tuple[str, ...]
) -> tuple[str, ...]:
    # TimeoutExpired は text=True でもバイト列を返す。中断で途切れた行を除く。
    try:
        complete = (
            stdout.rpartition(b"\n")[0].decode("utf-8")
            if isinstance(stdout, bytes)
            else (stdout or "").rpartition("\n")[0]
        )
    except UnicodeDecodeError:
        return ()
    return inspect_output(complete, max_nesting, selected_names).read_servers
