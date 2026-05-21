"""External MCP connector registry for the Telegram Claude supervisor."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .config import Config


BASE_SUPERVISOR_ALLOWED_TOOLS: tuple[str, ...] = (
    "mcp__supervisor__list_runs",
    "mcp__supervisor__resolve_session",
    "mcp__supervisor__read_recent_events",
    "mcp__supervisor__read_hooks",
    "mcp__supervisor__read_actions",
    "mcp__supervisor__read_supervisor_turns",
    "mcp__supervisor__read_run_timeline",
    "mcp__supervisor__watch_run",
    "mcp__supervisor__request_steering",
    "mcp__supervisor__list_run_watches",
    "mcp__supervisor__read_workspace_snapshot",
    "mcp__supervisor__read_workspace_file",
    "mcp__supervisor__evaluate_scope",
    "mcp__supervisor__propose_action",
)


def load_claude_desktop_mcp_servers(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load `mcpServers` from Claude Desktop's JSON config if present."""
    p = Path(path).expanduser()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
    except Exception:
        return {}
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        return {}
    return {
        str(name): copy.deepcopy(config)
        for name, config in servers.items()
        if isinstance(config, dict)
    }


def external_mcp_servers(cfg: Config) -> dict[str, dict[str, Any]]:
    """Return configured external MCP servers, excluding the local supervisor."""
    if not cfg.connectors.enabled:
        return {}
    servers: dict[str, dict[str, Any]] = {}
    if cfg.connectors.import_from_claude_desktop:
        servers.update(load_claude_desktop_mcp_servers(
            cfg.connectors.claude_desktop_config_path
        ))
    servers.update(copy.deepcopy(cfg.connectors.mcp_servers or {}))
    servers.pop("supervisor", None)
    return servers


def connector_allowed_tools(cfg: Config) -> list[str]:
    """Allowed tools for Claude Agent SDK.

    External connectors are inert until a tool is explicitly allowlisted. This
    lets users add MCP server connection configs before deciding which actions
    Telegram Claude may call automatically.
    """
    out = list(BASE_SUPERVISOR_ALLOWED_TOOLS)
    if cfg.connectors.enabled:
        out.extend(str(t) for t in cfg.connectors.allowed_tools)
    return _dedupe(out)


def connector_disallowed_tools(cfg: Config) -> list[str]:
    if not cfg.connectors.enabled:
        return []
    return _dedupe(str(t) for t in cfg.connectors.disallowed_tools)


def _dedupe(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen
