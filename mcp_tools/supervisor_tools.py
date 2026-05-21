"""MCP wrappers for the conversational supervisor tool API."""
from __future__ import annotations

import json
from typing import Any

from supervisor.config import Config
from supervisor.state import State
from supervisor.supervisor_tools import SupervisorToolAPI


def build_supervisor_mcp_server(cfg: Config, state: State, api: SupervisorToolAPI | None = None):
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except ModuleNotFoundError as e:
        if e.name == "claude_agent_sdk":
            raise
        raise

    tool_api = api or SupervisorToolAPI(state)

    def _content(payload: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}

    @tool("list_runs", "List tracked Codex Desktop runs.", {"limit": int, "include_completed": bool})
    async def list_runs(args: dict) -> dict[str, Any]:
        return _content(tool_api.list_runs(
            limit=int(args.get("limit") or 10),
            include_completed=bool(args.get("include_completed", True)),
        ))

    @tool("resolve_session", "Resolve a human session name such as 'Vela chat bot'.", {"name": str})
    async def resolve_session(args: dict) -> dict[str, Any]:
        return _content(tool_api.resolve_session(str(args.get("name") or "")))

    @tool("read_recent_events", "Read recent normalized events for a run or named session.", {"run_id": str, "session_name": str, "n": int})
    async def read_recent_events(args: dict) -> dict[str, Any]:
        return _content(tool_api.read_recent_events(
            run_id=args.get("run_id") or None,
            session_name=args.get("session_name") or None,
            n=int(args.get("n") or 20),
        ))

    @tool("read_hooks", "Read recent hook audit rows for a run.", {"run_id": str, "n": int})
    async def read_hooks(args: dict) -> dict[str, Any]:
        return _content(tool_api.read_hooks(run_id=str(args["run_id"]), n=int(args.get("n") or 20)))

    @tool("read_actions", "Read recent action ledger rows for a run.", {"run_id": str, "n": int})
    async def read_actions(args: dict) -> dict[str, Any]:
        return _content(tool_api.read_actions(run_id=str(args["run_id"]), n=int(args.get("n") or 20)))

    @tool("read_supervisor_turns", "Read recent Telegram supervisor turns for traceback.", {"chat_id": str, "n": int})
    async def read_supervisor_turns(args: dict) -> dict[str, Any]:
        return _content(tool_api.read_supervisor_turns(
            chat_id=str(args.get("chat_id") or ""),
            n=int(args.get("n") or 10),
        ))

    @tool("read_run_timeline", "Read high-signal timeline cards for a run or named session.", {"run_id": str, "session_name": str, "n": int})
    async def read_run_timeline(args: dict) -> dict[str, Any]:
        return _content(tool_api.read_run_timeline(
            run_id=args.get("run_id") or None,
            session_name=args.get("session_name") or None,
            n=int(args.get("n") or 80),
        ))

    @tool("watch_run", "Watch a run or named session and stream high-signal progress to Telegram.", {"chat_id": str, "run_id": str, "session_name": str})
    async def watch_run(args: dict) -> dict[str, Any]:
        return _content(tool_api.watch_run(
            chat_id=str(args.get("chat_id") or cfg.telegram.chat_id),
            run_id=args.get("run_id") or None,
            session_name=args.get("session_name") or None,
        ))

    @tool("request_steering", "Request a steering message for a run or named session. In advise mode this creates a Telegram approval prompt; in enforce mode it may auto-deliver non-destructive steering.", {"run_id": str, "session_name": str, "message": str})
    async def request_steering(args: dict) -> dict[str, Any]:
        return _content(await tool_api.request_steering_async(
            run_id=args.get("run_id") or None,
            session_name=args.get("session_name") or None,
            message=str(args.get("message") or ""),
        ))

    @tool("list_run_watches", "List durable Telegram watches for runs.", {"chat_id": str, "run_id": str})
    async def list_run_watches(args: dict) -> dict[str, Any]:
        return _content(tool_api.list_run_watches(
            chat_id=args.get("chat_id") or None,
            run_id=args.get("run_id") or None,
        ))

    @tool("read_workspace_snapshot", "Read redacted git status/diff for a run workspace.", {"run_id": str, "max_diff_chars": int})
    async def read_workspace_snapshot(args: dict) -> dict[str, Any]:
        return _content(tool_api.read_workspace_snapshot(
            run_id=str(args["run_id"]),
            max_diff_chars=int(args.get("max_diff_chars") or 12_000),
        ))

    @tool("read_workspace_file", "Read a redacted file under the run workspace root.", {"run_id": str, "path": str, "max_bytes": int})
    async def read_workspace_file(args: dict) -> dict[str, Any]:
        return _content(tool_api.read_workspace_file(
            run_id=str(args["run_id"]),
            path=str(args["path"]),
            max_bytes=int(args.get("max_bytes") or 20_000),
        ))

    @tool("evaluate_scope", "Evaluate current scope findings for a run.", {"run_id": str, "n": int})
    async def evaluate_scope(args: dict) -> dict[str, Any]:
        return _content(tool_api.evaluate_scope(run_id=str(args["run_id"]), n=int(args.get("n") or 30)))

    @tool("propose_action", "Convert findings into mode-safe proposed actions. Does not execute them.", {"run_id": str, "findings": list})
    async def propose_action(args: dict) -> dict[str, Any]:
        findings = args.get("findings") if isinstance(args.get("findings"), list) else []
        return _content(tool_api.propose_action(run_id=str(args["run_id"]), findings=findings))

    return create_sdk_mcp_server(
        name="supervisor",
        version="0.1.0",
        tools=[
            list_runs,
            resolve_session,
            read_recent_events,
            read_hooks,
            read_actions,
            read_supervisor_turns,
            read_run_timeline,
            watch_run,
            request_steering,
            list_run_watches,
            read_workspace_snapshot,
            read_workspace_file,
            evaluate_scope,
            propose_action,
        ],
    )
