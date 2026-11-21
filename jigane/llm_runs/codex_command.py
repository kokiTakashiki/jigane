"""端末の Codex に渡すモデル、資料参照、出力先の起動設定。"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from jigane.llm_runs import codex_mcp

if TYPE_CHECKING:
    from jigane.llm_runs.codex import Model

NO_PROXY_CONFIG = ("--disable", "respect_system_proxy", "--disable", "system_proxy_fallback")

_DISABLED_FEATURES = (
    "shell_tool",
    "shell_snapshot",
    "shell_snapshot_v2",
    "code_mode_host",
    "view_image",
    "multi_agent",
    "goals",
    "image_generation",
    "multi_agent_v2",
    "default_mode_request_user_input",
    "plugins",
    "remote_plugin",
    "apps",
    "hooks",
    "browser_use",
    "computer_use",
    "memories",
    "unbounded_connection_retries",
    "tool_call_mcp_elicitation",
    "skill_mcp_dependency_install",
)


def _reference_config(
    servers: tuple[codex_mcp.StdioServer, ...], directory: Path, model: "Model"
) -> list[str]:
    config = ["-c", codex_mcp.server_override(servers)]
    if not servers:
        return config

    # モデルのカタログに Code Mode や編集の道具が指定されても、資料の組み立てには要らない。
    # モデル名と認証経路を保ち、Codex CLI が参照の関数を直接公開する構成をこの起動だけに渡す。
    catalog = directory / "reference-model.json"
    definition = {
        "slug": model.name,
        "display_name": model.name,
        "default_reasoning_level": model.effort,
        "supported_reasoning_levels": (
            [] if model.effort is None else [{"effort": model.effort, "description": "Review"}]
        ),
        "visibility": "list",
        "supported_in_api": True,
        "priority": 1,
        "shell_type": "disabled",
        "tool_mode": "direct",
        "experimental_supported_tools": [],
        "include_apps_usage_instructions": False,
        "use_responses_lite": True,
        "support_verbosity": True,
        "default_verbosity": "low",
        "context_window": model.short_context_limit,
        "truncation_policy": {"mode": "tokens", "limit": 10_000},
        "model_messages": {"instructions_template": "Perform the requested static review."},
    }
    catalog.write_text(json.dumps({"models": [definition]}), encoding="utf-8")
    return [
        *config,
        "-c",
        f"model_catalog_json={json.dumps(str(catalog), ensure_ascii=False)}",
    ]


def exec_argv(
    directory: Path,
    model: "Model",
    provider_config: tuple[str, ...],
    servers: tuple[codex_mcp.StdioServer, ...],
    *,
    schema: Path,
    output: Path,
) -> list[str]:
    return [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--sandbox",
        "read-only",
        *(arg for feature in _DISABLED_FEATURES for arg in ("--disable", feature)),
        *NO_PROXY_CONFIG,
        "-c",
        'approval_policy="never"',
        "-c",
        'web_search="disabled"',
        "-c",
        "tools={experimental_request_user_input={enabled=false},update_plan={enabled=false}}",
        *provider_config,
        "-c",
        "analytics.enabled=false",
        "-c",
        'otel.exporter="none"',
        "-c",
        'otel.trace_exporter="none"',
        "-c",
        'otel.metrics_exporter="none"',
        "-c",
        "check_for_update_on_startup=false",
        "-c",
        "project_doc_max_bytes=0",
        "-c",
        "project_root_markers=[]",
        "-c",
        "skills.include_instructions=false",
        "-c",
        "features.skip_host_skill_discovery=true",
        "-c",
        'history.persistence="none"',
        "-c",
        f"sqlite_home={json.dumps(str(directory / 'state'), ensure_ascii=False)}",
        "-c",
        f"log_dir={json.dumps(str(directory / 'log'), ensure_ascii=False)}",
        "--model",
        model.name,
        *(
            ["-c", f'model_reasoning_effort="{model.effort}"', "-c", 'service_tier="default"']
            if model.effort is not None
            else []
        ),
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(output),
        "--json",
        *_reference_config(servers, directory, model),
        "-",
    ]
