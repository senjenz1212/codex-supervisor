from __future__ import annotations

import json

from supervisor.config import Config
from supervisor.connectors import (
    BASE_SUPERVISOR_ALLOWED_TOOLS,
    connector_allowed_tools,
    external_mcp_servers,
    load_claude_desktop_mcp_servers,
)
from supervisor.state import State
from supervisor.telegram_supervisor import ClaudeAgentSupervisorRuntime
from supervisor.supervisor_tools import SupervisorToolAPI


def _cfg(tmp_path, *, connectors: dict | None = None) -> Config:
    return Config(**{
        "target": {
            "kind": "codex",
            "codex": {
                "sessions_root": str(tmp_path / "sessions"),
                "cli_command": "codex",
                "desktop_process_names": ["Codex"],
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-opus-4-7",
            "drift_l3_model": "claude-opus-4-7",
            "drift_l4_model": "claude-opus-4-7",
            "post_run_eval_model": "claude-opus-4-7",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "123456:test-token", "chat_id": "42"},
        "connectors": connectors or {},
    })


def test_load_claude_desktop_mcp_servers_reads_only_mcp_servers(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    path.write_text(json.dumps({
        "theme": "dark",
        "mcpServers": {
            "slack": {
                "type": "http",
                "url": "https://mcp.slack.com/mcp",
                "headers": {"Authorization": "Bearer ${SLACK_MCP_TOKEN}"},
            }
        },
    }))

    servers = load_claude_desktop_mcp_servers(path)

    assert sorted(servers) == ["slack"]
    assert servers["slack"]["type"] == "http"


def test_external_connectors_are_inert_until_enabled_and_tool_allowlisted(tmp_path):
    cfg = _cfg(tmp_path, connectors={
        "enabled": False,
        "mcp_servers": {
            "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
        },
        "allowed_tools": ["mcp__slack__search"],
    })

    assert external_mcp_servers(cfg) == {}
    assert connector_allowed_tools(cfg) == list(BASE_SUPERVISOR_ALLOWED_TOOLS)


def test_connector_registry_merges_claude_desktop_and_local_servers(tmp_path):
    desktop = tmp_path / "claude_desktop_config.json"
    desktop.write_text(json.dumps({
        "mcpServers": {
            "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
        },
    }))
    cfg = _cfg(tmp_path, connectors={
        "enabled": True,
        "import_from_claude_desktop": True,
        "claude_desktop_config_path": str(desktop),
        "mcp_servers": {
            "drive": {"type": "http", "url": "https://example.test/mcp"},
        },
        "allowed_tools": ["mcp__slack__search", "mcp__drive__search"],
    })

    assert sorted(external_mcp_servers(cfg)) == ["drive", "slack"]
    assert "mcp__slack__search" in connector_allowed_tools(cfg)
    assert "mcp__drive__search" in connector_allowed_tools(cfg)


def test_telegram_runtime_builds_options_with_connector_mcp_servers(tmp_path):
    cfg = _cfg(tmp_path, connectors={
        "enabled": True,
        "mcp_servers": {
            "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
        },
        "allowed_tools": ["mcp__slack__search"],
        "disallowed_tools": ["mcp__slack__send_message"],
    })
    state = State(str(tmp_path / "state.db"))
    runtime = ClaudeAgentSupervisorRuntime(cfg, state)

    options = runtime._build_options(SupervisorToolAPI(state))

    assert sorted(options.mcp_servers) == ["slack", "supervisor"]
    assert "mcp__supervisor__list_runs" in options.allowed_tools
    assert "mcp__supervisor__watch_run" in options.allowed_tools
    assert "mcp__supervisor__request_steering" in options.allowed_tools
    assert "mcp__supervisor__list_run_watches" in options.allowed_tools
    assert "mcp__supervisor__read_workspace_snapshot" in options.allowed_tools
    assert "mcp__supervisor__read_workspace_file" in options.allowed_tools
    assert "mcp__supervisor__request_mode_change" not in options.allowed_tools
    assert "mcp__slack__search" in options.allowed_tools
    assert "mcp__slack__send_message" in options.disallowed_tools
    assert options.permission_mode == "dontAsk"


def test_telegram_runtime_resumes_only_valid_claude_session_ids(tmp_path):
    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    runtime = ClaudeAgentSupervisorRuntime(cfg, state)
    api = SupervisorToolAPI(state)

    valid = "019e4f33-1a30-7337-9d9b-0f34c5da0001"
    options = runtime._build_options(
        api,
        conversation_context={"claude_session_id": valid},
    )
    invalid_options = runtime._build_options(
        api,
        conversation_context={"claude_session_id": "not-a-sdk-session-id"},
    )

    assert options.resume == valid
    assert invalid_options.resume is None
