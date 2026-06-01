"""CLI fallback for the supervisor-owned dual-agent workflow.

The primary path is the Codex MCP tool. This module gives operators a second
transport that calls the same CodexSupervisorMcpAPI directly, writes a durable
JSON result, and does not require a live MCP session.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI, Runner
from supervisor.config import Config
from supervisor.cursor_agent import CursorRunner
from supervisor.state import State


WORKFLOW_KEYS = {
    "cwd",
    "task_id",
    "run_id",
    "intent",
    "user_facing",
    "max_rounds_per_gate",
    "quality",
    "budget_usd",
    "timeout_s",
    "execution_layer_mode",
    "dynamic_workflow_task_class",
    "agentic_lead_policy",
    "min_subagents",
    "required_roles",
    "solo_exception_for_artifact_only_gates",
    "required_evidence_grade",
    "reviewer_unavailable_policy",
    "planning_artifacts",
    "screenshots",
    "verified_claims",
    "tool_receipts",
    "require_skill_receipts",
    "cursor_review",
    "cursor_review_profile",
    "cursor_review_gates",
    "cursor_model",
    "reviewer_model",
    "reviewer_output_mode",
    "reviewer_max_tokens",
    "reviewer_infra_retry_limit",
    "reviewer_infra_retry_backoff_s",
    "task_complexity",
}
REQUIRED_WORKFLOW_KEYS = {"cwd", "task_id", "run_id", "intent"}


def workflow_kwargs_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_WORKFLOW_KEYS - set(payload))
    if missing:
        raise ValueError(f"missing required workflow fields: {', '.join(missing)}")
    return {key: payload[key] for key in WORKFLOW_KEYS if key in payload}


async def run_workflow_payload(
    payload: dict[str, Any],
    *,
    cfg: Config,
    state: State,
    runner: Runner = subprocess.run,
    codex_runner: Runner = subprocess.run,
    cursor_runner: CursorRunner | None = None,
    notifier: Any | None = None,
) -> dict[str, Any]:
    api = CodexSupervisorMcpAPI(
        cfg,
        state,
        runner=runner,
        codex_runner=codex_runner,
        cursor_runner=cursor_runner,
        notifier=notifier,
    )
    return await api.run_dual_agent_workflow(**workflow_kwargs_from_payload(payload))


def load_secrets_env(path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        cleaned = value.strip().strip("'\"")
        os.environ[key] = cleaned
        loaded[key] = cleaned
    return loaded


def load_codex_mcp_env(path: Path, *, server_name: str = "codex_supervisor") -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    section = f"[mcp_servers.{server_name}.env]"
    active = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            active = line == section
            continue
        if not active or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        cleaned = value.strip().strip("'\"")
        os.environ[key] = cleaned
        loaded[key] = cleaned
    return loaded


def read_json_payload(path: str) -> dict[str, Any]:
    text = sys.stdin.read() if path == "-" else Path(path).expanduser().read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("workflow request must be a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def persist_detached_workflow_terminal_outcome(
    *,
    request_payload: dict[str, Any],
    result: dict[str, Any],
    state: State,
    output_path: Path | None = None,
    returncode: int | None = None,
    error: str | None = None,
) -> bool:
    """Persist a detached workflow worker's terminal result to the ledger."""
    raw_job_id = request_payload.get("job_id")
    job_id = str(raw_job_id).strip() if raw_job_id else ""
    if not job_id and output_path is not None:
        parent_name = output_path.expanduser().parent.name
        if parent_name.startswith("workflow-"):
            job_id = parent_name
    if not job_id:
        return False
    status = str(result.get("status") or "completed")
    state.complete_dual_agent_workflow_job(
        job_id=job_id,
        status=status,
        terminal_outcome=result,
        returncode=returncode,
        error=error,
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a dual-agent workflow without MCP transport.")
    parser.add_argument("--config", default=str(Path.home() / ".codex-supervisor" / "config.yaml"))
    parser.add_argument("--request", default="-", help="JSON request file, or '-' for stdin.")
    parser.add_argument("--output", help="Optional path to write the JSON workflow result.")
    parser.add_argument(
        "--secrets",
        default=str(Path.home() / ".codex-supervisor" / "secrets.env"),
        help="Optional dotenv-style secrets file loaded before the workflow starts.",
    )
    parser.add_argument("--no-secrets", action="store_true", help="Do not load the secrets file.")
    parser.add_argument(
        "--codex-config",
        default=str(Path.home() / ".codex" / "config.toml"),
        help="Codex config whose MCP env block can supply Cursor/agent keys.",
    )
    parser.add_argument(
        "--no-codex-mcp-env",
        action="store_true",
        help="Do not load env vars from [mcp_servers.codex_supervisor.env].",
    )
    parser.add_argument(
        "--fail-on-blocked",
        action="store_true",
        help="Exit nonzero when the supervisor verdict is not accepted.",
    )
    args = parser.parse_args(argv)

    if not args.no_codex_mcp_env:
        load_codex_mcp_env(Path(args.codex_config).expanduser())
    if not args.no_secrets:
        load_secrets_env(Path(args.secrets).expanduser())

    cfg = Config.load(args.config)
    state = State(cfg.supervisor.state_db)
    request = read_json_payload(args.request)
    result = asyncio.run(run_workflow_payload(request, cfg=cfg, state=state))
    exit_code = 2 if args.fail_on_blocked and result.get("status") != "accepted" else 0
    output_path = Path(args.output).expanduser() if args.output else None

    persist_detached_workflow_terminal_outcome(
        request_payload=request,
        result=result,
        state=state,
        output_path=output_path,
        returncode=exit_code,
        error="" if exit_code == 0 else "workflow_not_accepted",
    )

    if output_path is not None:
        write_json(output_path, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
