"""Codex MCP toolpack.

In-process MCP server registered with ClaudeSDKClient. Exposes the operations
the supervisor's skills need to interact with running Codex sessions.
"""
from __future__ import annotations
import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from supervisor.config import Config
from supervisor.state import State
from supervisor.target.factory import build_target_adapter
from supervisor.workspace_grounding import read_workspace_file as read_grounded_workspace_file
from supervisor.workspace_grounding import workspace_snapshot

log = logging.getLogger(__name__)


def build_codex_mcp_server(cfg: Config, state: State):
    """Returns an in-process MCP server with the Codex toolpack."""
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ModuleNotFoundError as e:
        if e.name == "claude_agent_sdk":
            raise
        raise

    @tool(
        "list_active_runs",
        "List currently-running Codex sessions tracked by the supervisor.",
        {},
    )
    async def list_active_runs(args: dict) -> dict[str, Any]:
        rows = state.active_runs()
        return {"content": [{"type": "text", "text": json.dumps(
            [{"run_id": r["run_id"], "session_id": r["session_id"],
              "task": r["task"], "started_at": r["started_at"]}
             for r in rows], indent=2)}]}

    @tool(
        "read_rollout",
        "Read recent events from a run's rollout. Returns the last N events.",
        {"run_id": str, "n": int},
    )
    async def read_rollout(args: dict) -> dict[str, Any]:
        n = int(args.get("n") or 30)
        events = state.recent_events(args["run_id"], n=n)
        return {"content": [{"type": "text",
                             "text": json.dumps(events, indent=2, default=str)}]}

    @tool(
        "get_run_metadata",
        "Get the original task description, scope hints, and run status.",
        {"run_id": str},
    )
    async def get_run_metadata(args: dict) -> dict[str, Any]:
        row = state.get_run(str(args["run_id"]))
        if row is None:
            return {"content": [{"type": "text", "text": '{"error": "run not found"}'}]}
        meta = {"run_id": row["run_id"], "session_id": row["session_id"],
                "task": row["task"], "scope_hints": row["scope_hints"],
                "status": row["status"], "started_at": row["started_at"]}
        return {"content": [{"type": "text", "text": json.dumps(meta, indent=2)}]}

    @tool(
        "read_workspace_snapshot",
        "Read a redacted, read-only git status/diff snapshot for the run workspace.",
        {"run_id": str, "max_diff_chars": int},
    )
    async def read_workspace_snapshot(args: dict) -> dict[str, Any]:
        payload = workspace_snapshot(
            state,
            run_id=str(args["run_id"]),
            max_diff_chars=int(args.get("max_diff_chars") or 12_000),
        )
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}

    @tool(
        "read_workspace_file",
        "Read a redacted file under the resolved run workspace root. Path traversal is denied.",
        {"run_id": str, "path": str, "max_bytes": int},
    )
    async def read_workspace_file(args: dict) -> dict[str, Any]:
        payload = read_grounded_workspace_file(
            state,
            run_id=str(args["run_id"]),
            path=str(args["path"]),
            max_bytes=int(args.get("max_bytes") or 20_000),
        )
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}

    @tool(
        "inject_steering",
        "Inject a steering turn into a running Codex session via `codex exec --resume`. "
        "Use this for soft nudges when the agent has drifted.",
        {"session_id": str, "message": str},
    )
    async def inject_steering(args: dict) -> dict[str, Any]:
        session_id = args["session_id"]
        message = args["message"]
        log.info("injecting steering into %s: %.80s", session_id, message)

        adapter = build_target_adapter(cfg)
        version = _codex_version(cfg)
        argv = adapter.build_resume_command(session_id, message, version)

        # Run the adapter-selected resume command in a subprocess; we don't
        # wait for the agent's full reply.
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Give it 5s to register the turn; we don't need to await completion.
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
                ok = proc.returncode == 0
            except asyncio.TimeoutError:
                ok = True  # The command is running; that's enough for "delivered".
        except FileNotFoundError:
            return {"content": [{"type": "text",
                                 "text": '{"error": "codex binary not on PATH"}'}],
                    "isError": True}

        # Record as an event so it shows up in the supervisor history.
        row = next((r for r in state.active_runs() if r["session_id"] == session_id), None)
        if row:
            state.write_event(run_id=row["run_id"], source="drift",
                              kind="steering_injected",
                              payload={"message": message})
        return {"content": [{"type": "text",
                             "text": json.dumps({"delivered": ok})}]}

    @tool(
        "record_decision",
        "Record the agent's final decision for this run (for replay and audit).",
        {"run_id": str, "decision_json": str},
    )
    async def record_decision(args: dict) -> dict[str, Any]:
        try:
            payload = json.loads(args["decision_json"])
        except Exception as e:
            payload = {"raw": args["decision_json"], "parse_error": str(e)}
        state.write_event(run_id=args["run_id"], source="drift",
                          kind="agent_decision", payload=payload)
        return {"content": [{"type": "text", "text": '{"ok": true}'}]}

    return create_sdk_mcp_server(
        name="codex",
        version="0.2.0",
        tools=[
            list_active_runs,
            read_rollout,
            get_run_metadata,
            read_workspace_snapshot,
            read_workspace_file,
            inject_steering,
            record_decision,
        ],
    )


def _codex_version(cfg: Config) -> str | None:
    target = getattr(cfg, "target", None)
    codex = getattr(target, "codex", None) if target is not None else None
    legacy = getattr(cfg, "codex", None)
    cli = getattr(codex or legacy, "cli_command", "codex")
    try:
        out = subprocess.run(
            [cli, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    text = (out.stdout or out.stderr or "").strip()
    for token in text.split():
        if token and token[0].isdigit():
            return token
    return None
