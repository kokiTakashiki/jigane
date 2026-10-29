"""claude の起動引数を固定する。群と道具の一覧は定義から列挙し、増えても検査が及ぶ。"""

import ast
import importlib
import inspect
import json
import pkgutil
from pathlib import Path
from typing import Any

import pytest

import jigane.skills
from jigane.groups.definitions import GROUPS, SECURITY, Group
from jigane.llm_runs import claude
from jigane.skills import macro, security
from jigane.skills.mcp import McpSelection, McpServer

# 読み取りの道具だけ。許可の一覧にして、未知の道具（BashOutput、Skill など）も落とす
ALLOWED_TOOLS = frozenset({"Read", "Grep", "Glob", *claude.MCP_RESOURCE_TOOLS})
FORBIDDEN_FLAGS = frozenset(
    {"--dangerously-skip-permissions", "--permission-mode", "--allow-dangerously-skip-permissions"}
)
SKILLS_DIRECTORY = Path(jigane.skills.__file__).parent


def skill_modules() -> list[Any]:
    return [
        importlib.import_module(f"jigane.skills.{info.name}")
        for info in pkgutil.iter_modules([str(SKILLS_DIRECTORY)])
    ]


def tool_sets() -> list[tuple[str, tuple[str, ...]]]:
    """skills の各モジュールが持つ *_TOOLS の定数と、起動が既定で渡す道具。"""
    found = {
        f"{module.__name__}.{name}": value
        for module in skill_modules()
        for name, value in vars(module).items()
        if name.endswith("_TOOLS") and name != "ALLOWED_MCP_TOOLS" and isinstance(value, tuple)
    }
    default = inspect.signature(claude.RunSettings.invocation).parameters["tools"].default
    return [("RunSettings.invocation の tools の既定", default), *found.items()]


def settings_of(group: Group) -> claude.RunSettings:
    def refuse(_invocation: claude.Invocation) -> claude.InvocationResult:
        raise AssertionError("起動しない")

    return claude.RunSettings(
        budget=claude.RunBudget(
            group.model, group.shape.max_budget_usd, group.shape.reserve_margin
        ),
        effort=group.shape.effort,
        invoke=refuse,
        max_turns=group.shape.max_turns,
    )


def command_for(
    group: Group,
    tmp_path: Path,
    tools: tuple[str, ...],
    mcp: tuple[Path, tuple[str, ...]] | None = None,
) -> list[str]:
    invocation = settings_of(group).invocation(
        prompt="p",
        json_schema={"type": "object"},
        working_directory=tmp_path,
        tools=tools,
        mcp_config=mcp[0] if mcp else None,
        allowed_mcp_tools=mcp[1] if mcp else (),
    )
    return claude.build_command(invocation)


def tools_argument(command: list[str]) -> list[str]:
    value = command[command.index("--tools") + 1]
    return value.split(",") if value else []


def tools_outside_allowed_list(tools: list[str]) -> list[str]:
    return sorted(set(tools) - ALLOWED_TOOLS)


def test_tool_check_flags_a_tool_outside_the_allowed_list() -> None:
    # ast-grep-ignore: no-claude-launch-outside-llm-runs 起動ではなく、期待する引数の列
    command = ["claude", "--tools", "Read,Skill,BashOutput", "-p"]
    assert tools_outside_allowed_list(tools_argument(command)) == ["BashOutput", "Skill"]


def test_tool_sets_are_discovered() -> None:
    names = [name for name, _ in tool_sets()]
    assert any(name.endswith("macro.DISCOVERY_TOOLS") for name in names)
    assert any(name.endswith("security.DISCOVERY_TOOLS") for name in names)


@pytest.mark.parametrize("group", GROUPS, ids=lambda group: group.identifier)
@pytest.mark.parametrize("name_and_tools", tool_sets(), ids=lambda pair: pair[0])
def test_every_launch_is_restricted_read_only_and_never_skips_permissions(
    group: Group, name_and_tools: tuple[str, tuple[str, ...]], tmp_path: Path
) -> None:
    _, tools = name_and_tools
    command = command_for(group, tmp_path, tools)
    for flag in ("--restricted", "-p", "--strict-mcp-config", "--max-budget-usd"):
        assert flag in command
    assert not FORBIDDEN_FLAGS & set(command)
    assert not tools_outside_allowed_list(tools_argument(command))
    settings = json.loads(command[command.index("--settings") + 1])
    deny = set(settings["permissions"]["deny"])
    for pattern in claude.SECRET_FILE_PATTERNS:
        assert {f"Read(./{pattern})", f"Read(**/{pattern})"} <= deny
    # MCP の設定は security だけが渡す。ここでは渡していない
    assert "--mcp-config" not in command
    assert "--allowedTools" not in command


@pytest.mark.parametrize(
    ("path", "secret"),
    [
        (".env", True),
        ("a/b/.env.production", True),
        ("keys/id_ecdsa", True),
        ("x.pfx", True),
        ("infra/prod.tfvars", True),
        ("terraform.tfstate", True),
        ("src/environment.py", False),
        ("docs/key.md", False),
    ],
)
def test_is_secret_file_matches_by_file_name(path: str, *, secret: bool) -> None:
    assert claude.is_secret_file(path) is secret


def test_security_mcp_launch_allows_only_selected_servers(tmp_path: Path) -> None:
    group = next(group for group in GROUPS if group.identifier == SECURITY)
    command = command_for(
        group,
        tmp_path,
        security.DISCOVERY_TOOLS,
        (
            tmp_path / "mcp.json",
            McpSelection((McpServer("mobile-sec", {}, None),)).allowed_tools(),
        ),
    )
    assert command[command.index("--mcp-config") + 1] == str(tmp_path / "mcp.json")
    allowed = command[command.index("--allowedTools") + 1].split(",")
    assert set(allowed) == {"mcp__mobile-sec", *claude.MCP_RESOURCE_TOOLS}
    assert not tools_outside_allowed_list(tools_argument(command))
    assert not FORBIDDEN_FLAGS & set(command)


def test_security_default_launch_passes_no_mcp_config(tmp_path: Path) -> None:
    group = next(group for group in GROUPS if group.identifier == SECURITY)
    command = command_for(group, tmp_path, macro.DISCOVERY_TOOLS)
    assert "--mcp-config" not in command
    assert "--allowedTools" not in command
    assert "--strict-mcp-config" in command
    assert not tools_outside_allowed_list(tools_argument(command))


def _keyword_names(call: ast.Call) -> set[str]:
    return {keyword.arg for keyword in call.keywords if keyword.arg is not None}


def test_only_security_passes_an_mcp_config() -> None:
    """MCP の設定を渡す定義は security だけ。他の群が足せば、ここで落ちる。"""
    passing: set[str] = set()
    for path in SKILLS_DIRECTORY.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _keyword_names(node) & {
                "mcp_config",
                "allowed_mcp_tools",
            }:
                passing.add(path.name)
    # sharded.py は ShardedGroup の欄を Invocation へ受け渡す側
    assert passing == {"security.py", "sharded.py"}


def test_every_invocation_site_takes_tools_from_a_named_constant() -> None:
    """起動を作る箇所が、道具を *_TOOLS の定数か既定でだけ決める。直書きは検査の外に出る。"""
    sites = 0
    for path in SKILLS_DIRECTORY.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "invocation"
            ):
                continue
            sites += 1
            for keyword in node.keywords:
                if keyword.arg != "tools":
                    continue
                value = keyword.value
                name = value.attr if isinstance(value, ast.Attribute) else getattr(value, "id", "")
                assert name.endswith("_TOOLS") or name == "tools", f"{path.name}:{node.lineno}"
    assert sites >= 4


def test_reviewer_environment_strips_every_github_token_name() -> None:
    parent = {
        "GH_TOKEN": "a",
        "GITHUB_TOKEN": "b",
        "GH_ENTERPRISE_TOKEN": "c",
        "GITHUB_ENTERPRISE_TOKEN": "d",
        "github_token": "e",
        "PATH": "/bin",
    }
    environment = claude.reviewer_environment(parent)
    assert environment["PATH"] == "/bin"
    assert not any("TOKEN" in name.upper() for name in environment)


def test_reviewer_environment_disables_nonessential_traffic_over_the_parent_value() -> None:
    assert claude.reviewer_environment({})["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    parent = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "0"}
    assert claude.reviewer_environment(parent)["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
