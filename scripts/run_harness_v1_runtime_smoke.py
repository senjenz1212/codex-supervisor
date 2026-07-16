#!/usr/bin/env python3
"""Run the same no-tool smoke task through Claude Code and Codex runtimes.

This is operational compatibility evidence only.  It does not measure coding
quality or support an efficacy, ROI, portability, or auto-improvement claim.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supervisor.agent_runtime import AgentTask, ClaudeCodeRuntime, CodexRuntime


INSTRUCTION = (
    "Reply with only OK. Do not inspect or modify files and do not use tools."
)


def _run(
    argv: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version(binary: str) -> str:
    completed = subprocess.run(
        [binary, "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    return (completed.stdout or completed.stderr).strip()


async def _run_one(
    runtime: ClaudeCodeRuntime | CodexRuntime,
    *,
    model: str,
    root: Path,
) -> dict[str, Any]:
    handle = await runtime.start(
        AgentTask(
            task_id=f"harness-v1-live-{runtime.kind}",
            instruction=INSTRUCTION,
            cwd=root,
            model=model,
            timeout_s=180,
            metadata={
                "permission_mode": "plan",
                "allowed_tools": [],
                "max_budget_usd": 0.25,
                "effort": "low",
                "result_metadata": {
                    "smoke_kind": "operational_runtime_compatibility",
                },
            },
        )
    )
    events = [event async for event in runtime.stream(handle)]
    result = await runtime.collect(handle)
    workspace_clean = not _run(
        ["git", "status", "--porcelain"],
        cwd=root,
    ).stdout.strip()
    result_dict = result.to_dict()
    return {
        "requested_model": model,
        "handle": {
            "run_id": handle.run_id,
            "task_id": handle.task_id,
            "runtime": handle.runtime,
            "session_id": handle.session_id,
            "capabilities": dict(handle.capabilities),
        },
        "event_kinds": [event.kind for event in events],
        "result_schema_keys": sorted(result_dict),
        "result": {
            "schema_version": result.schema_version,
            "run_id": result.run_id,
            "session_id": result.session_id,
            "status": result.status,
            "output": result.output,
            "output_sha256": hashlib.sha256(
                result.output.encode("utf-8")
            ).hexdigest(),
            "result_hash": result.result_hash,
            "resolved_model": result.resolved_model,
            "model_provenance": result.model_provenance,
            "cost_usd": result.cost_usd,
            "cost_provenance": result.cost_provenance,
            "token_usage": dict(result.token_usage),
            "token_provenance": result.token_provenance,
            "duration_ms": result.duration_ms,
            "provenance_support": dict(
                result.metadata.get("provenance_support") or {}
            ),
        },
        "workspace_clean_after": workspace_clean,
    }


def _prepare_task_repo(task_root: Path) -> str:
    _run(["git", "init", "--quiet"], cwd=task_root)
    _run(
        ["git", "config", "user.email", "harness@example.invalid"],
        cwd=task_root,
    )
    _run(["git", "config", "user.name", "Harness"], cwd=task_root)
    (task_root / "README.md").write_text(
        "runtime compatibility smoke\n",
        encoding="utf-8",
    )
    _run(["git", "add", "-A"], cwd=task_root)
    _run(["git", "commit", "--quiet", "-m", "base"], cwd=task_root)
    return _run(
        ["git", "rev-parse", "HEAD"],
        cwd=task_root,
    ).stdout.strip()


async def _main(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(
        prefix="harness-v1-runtime-smoke-claude-"
    ) as claude_dir, tempfile.TemporaryDirectory(
        prefix="harness-v1-runtime-smoke-codex-"
    ) as codex_dir:
        claude_root = Path(claude_dir)
        codex_root = Path(codex_dir)
        claude_revision = _prepare_task_repo(claude_root)
        codex_revision = _prepare_task_repo(codex_root)

        claude = await _run_one(
            ClaudeCodeRuntime(),
            model=args.claude_model,
            root=claude_root,
        )
        codex = await _run_one(
            CodexRuntime(),
            model=args.codex_model,
            root=codex_root,
        )

    head = _run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
    ).stdout.strip()
    dirty = bool(
        _run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
        ).stdout.strip()
    )
    same_result_schema = (
        claude["result_schema_keys"] == codex["result_schema_keys"]
    )
    exact_output = (
        claude["result"]["output"].strip() == "OK"
        and codex["result"]["output"].strip() == "OK"
    )
    both_completed = (
        claude["result"]["status"] == "completed"
        and codex["result"]["status"] == "completed"
    )
    both_workspaces_clean = (
        claude["workspace_clean_after"] and codex["workspace_clean_after"]
    )
    receipt = {
        "schema_version": "harness-v1-runtime-operational-smoke/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": {
            "operational_runtime_compatibility": True,
            "isolated_experiment_arm_execution": False,
            "coding_outcome_efficacy": False,
            "roi": False,
            "auto_improvement": False,
        },
        "repository": {
            "head": head,
            "worktree_dirty": dirty,
            "source_sha256": {
                "supervisor/agent_runtime.py": _sha256(
                    repo_root / "supervisor" / "agent_runtime.py"
                ),
                "supervisor/process_containment.py": _sha256(
                    repo_root / "supervisor" / "process_containment.py"
                ),
                "scripts/run_harness_v1_runtime_smoke.py": _sha256(
                    Path(__file__).resolve()
                ),
            },
        },
        "cli_versions": {
            "claude": _version("claude"),
            "codex": _version("codex"),
        },
        "task": {
            "instruction_sha256": hashlib.sha256(
                INSTRUCTION.encode("utf-8")
            ).hexdigest(),
            "repository_revision": {
                "claude_code": claude_revision,
                "codex": codex_revision,
            },
        },
        "runs": {
            "claude_code": claude,
            "codex": codex,
        },
        "same_result_schema": same_result_schema,
        "both_completed": both_completed,
        "exact_output_ok": exact_output,
        "both_workspaces_clean": both_workspaces_clean,
    }
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "same_result_schema": same_result_schema,
                "both_completed": both_completed,
                "exact_output_ok": exact_output,
                "both_workspaces_clean": both_workspaces_clean,
                "claude_resolved_model": claude["result"]["resolved_model"],
                "codex_resolved_model": codex["result"]["resolved_model"],
            },
            sort_keys=True,
            indent=2,
        )
    )
    return (
        0
        if same_result_schema
        and both_completed
        and exact_output
        and both_workspaces_clean
        else 1
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--claude-model",
        default="claude-haiku-4-5-20251001",
    )
    parser.add_argument("--codex-model", default="gpt-5.6-sol")
    parser.add_argument(
        "--output",
        default=(
            "docs/dual-agent/runtime-001-seams-20260711/"
            "operational-smoke-receipt.json"
        ),
    )
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
