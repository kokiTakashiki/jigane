"""Windows でも Claude Code が元の設定ファイルを直接受け取る。"""

from pathlib import Path

import pytest

from jigane.llm_runs import claude
from jigane.skills.mcp import MCP_CONFIG_DIRECTORY, McpSelection


def test_native_mcp_path_is_passed_as_one_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "data with spaces"
    monkeypatch.setenv("JIGANE_HOME", str(home))
    path = home / MCP_CONFIG_DIRECTORY / "refs.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"mcpServers": {"refs": {"command": "fixture"}}}', encoding="utf-8")
    selection = McpSelection.load(("refs",))
    invocation = claude.Invocation(
        "prompt",
        {},
        tmp_path,
        claude.LaunchBudget(0.1, 0.09),
        "test",
        mcp_configs=selection.config_paths,
        mcp_server_names=selection.names,
    )
    argv = claude.build_command(invocation)
    assert argv[argv.index("--mcp-config") + 1] == str(path.resolve())
