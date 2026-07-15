"""Supervisor-owned execution and bookkeeping for agentic lead worker processes."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from .agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    RuntimeEvent,
)
from .agentic_legacy_provider_edge import (
    LegacyRunner,
    execute_legacy_agent_task,
)
from .runtime_execution import (
    RuntimeExecution,
    RuntimeFactory,
    RuntimeTaskRunner,
    runtime_task_runner,
)


PidProbe = Callable[[int], bool]
Terminator = Callable[[int, int], None]
WorkerRunner = LegacyRunner


@dataclass(frozen=True)
class AgenticWorkerSpec:
    task_id: str
    worker_id: str
    role: str
    command: tuple[str, ...]
    cwd: str | Path
    instruction: str = ""
    model: str = ""
    persona_id: str = ""
    agent_runtime: str = "runtime"
    agent_id: str = ""
    permission_mode: str = "readOnly"
    tool_pins: tuple[str, ...] = field(default_factory=tuple)
    timeout_s: int = 600
    budget_usd: float = 0.0
    runtime_env: Mapping[str, str] = field(default_factory=dict)
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)


def worker_log_ref(*, cwd: str | Path, task_id: str, worker_id: str) -> str:
    """Return the durable supervisor-owned worker log ref relative to cwd."""
    safe_task = _safe_segment(task_id)
    safe_worker = _safe_segment(worker_id)
    return str(Path(".handoff") / "agentic-workers" / safe_task / safe_worker / "worker.log")


def worker_runtime_ref(*, cwd: str | Path, task_id: str, worker_id: str) -> str:
    """Return the durable worker runtime metadata ref relative to cwd."""
    safe_task = _safe_segment(task_id)
    safe_worker = _safe_segment(worker_id)
    return str(Path(".handoff") / "agentic-workers" / safe_task / safe_worker / "runtime.json")


def run_agentic_worker(
    spec: AgenticWorkerSpec,
    *,
    runtime_runner: RuntimeTaskRunner | None = None,
    runtime_factory: RuntimeFactory | None = None,
    runner: WorkerRunner | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run one worker and return a replay-verifiable normalized receipt.

    ``runtime_runner``/``runtime_factory`` are the production seam.  ``runner``
    is retained only as an explicit legacy provider-edge fallback.
    """
    task_runner = _resolve_runtime_runner(
        runtime_runner=runtime_runner,
        runtime_factory=runtime_factory,
    )
    if task_runner is not None and runner is not None:
        raise ValueError(
            "choose a runtime runner/factory or the legacy subprocess runner"
        )
    if task_runner is None and runner is None:
        raise ValueError(
            "agentic worker execution requires a runtime runner/factory; "
            "pass runner= only for the explicit legacy fallback"
        )

    cwd_path = Path(spec.cwd).resolve()
    worker_dir = cwd_path / ".handoff" / "agentic-workers" / _safe_segment(spec.task_id) / _safe_segment(spec.worker_id)
    worker_dir.mkdir(parents=True, exist_ok=True)
    started_at_s = now()
    runtime_path = worker_dir / "runtime.json"
    _write_worker_file(
        cwd_path,
        runtime_path,
        json.dumps(
            {
                "schema_version": "agentic-worker-runtime/v1",
                "task_id": spec.task_id,
                "worker_id": spec.worker_id,
                "role": spec.role,
                "pid": None,
                "status": "running",
                "started_at_s": started_at_s,
                "timeout_s": spec.timeout_s,
                "budget_usd": spec.budget_usd,
                "requested_model": spec.model,
                "log_ref": worker_log_ref(cwd=cwd_path, task_id=spec.task_id, worker_id=spec.worker_id),
            },
            sort_keys=True,
            indent=2,
        ) + "\n",
    )

    task = _worker_agent_task(spec, cwd=cwd_path)
    try:
        execution = (
            task_runner(task)
            if task_runner is not None
            else execute_legacy_agent_task(
                task,
                runner=runner,
                command=spec.command,
            )
        )
        _validate_runtime_execution(execution, task=task)
    except asyncio.CancelledError:
        _write_worker_file(
            cwd_path,
            runtime_path,
            json.dumps(
                {
                    "schema_version": "agentic-worker-runtime/v1",
                    "task_id": spec.task_id,
                    "worker_id": spec.worker_id,
                    "role": spec.role,
                    "pid": None,
                    "status": "cancelled",
                    "started_at_s": started_at_s,
                    "ended_at_s": now(),
                    "timeout_s": spec.timeout_s,
                    "budget_usd": spec.budget_usd,
                    "requested_model": spec.model,
                    "log_ref": worker_log_ref(cwd=cwd_path, task_id=spec.task_id, worker_id=spec.worker_id),
                },
                sort_keys=True,
                indent=2,
            ) + "\n",
        )
        raise
    except Exception as exc:
        execution = _failed_runtime_execution(
            task,
            exc=exc,
            runtime=spec.agent_runtime,
            started_at_ms=int(started_at_s * 1000),
            ended_at_ms=int(now() * 1000),
        )
    result = execution.result
    status = _worker_status(result)
    error = _runtime_error(result, status=status, timeout_s=spec.timeout_s)
    exit_code = _int(result.metadata.get("returncode"))
    ended_at_s = result.ended_at_ms / 1000
    stdout_ref = _write_worker_file(
        cwd_path,
        worker_dir / "stdout.txt",
        result.output,
    )
    stderr_ref = _write_worker_file(
        cwd_path,
        worker_dir / "stderr.txt",
        str(result.metadata.get("stderr") or ""),
    )
    output_payload = {
        "schema_version": "agentic-worker-output/v1",
        "task_id": spec.task_id,
        "worker_id": spec.worker_id,
        "role": spec.role,
        "status": status,
        "exit_code": exit_code,
        "error": error,
        "persona_id": spec.persona_id,
        "agent_runtime": result.runtime,
        "runtime": result.runtime,
        "runtime_run_id": result.run_id,
        "session_id": result.session_id,
        "resolved_model": result.resolved_model,
        "cost_usd": float(result.cost_usd),
        "token_usage": dict(result.token_usage),
        "result_hash": result.result_hash,
        "runtime_status": result.status,
        "model_provenance": result.model_provenance,
        "cost_provenance": result.cost_provenance,
        "token_provenance": result.token_provenance,
        "agent_run_result": _agent_run_result_payload(result),
        "agent_id": spec.agent_id or spec.worker_id,
        "permission_mode": spec.permission_mode,
        "tool_pins": list(spec.tool_pins),
        "timeout_s": spec.timeout_s,
        "budget_usd": spec.budget_usd,
        "stdout_ref": stdout_ref,
        "stderr_ref": stderr_ref,
    }
    output_ref = _write_worker_file(
        cwd_path,
        worker_dir / "output.json",
        json.dumps(output_payload, sort_keys=True, indent=2) + "\n",
    )
    transcript_events = [
        {
            "event": "worker_started",
            "task_id": spec.task_id,
            "worker_id": spec.worker_id,
            "role": spec.role,
            "started_at_s": started_at_s,
            "timeout_s": spec.timeout_s,
            "budget_usd": spec.budget_usd,
            "runtime_task_id": task.task_id,
            "requested_model": task.model,
        },
        *[
            {
                "event": "runtime_event",
                **event.to_dict(),
            }
            for event in result.events
        ],
        {
            "event": "worker_finished",
            "task_id": spec.task_id,
            "worker_id": spec.worker_id,
            "status": status,
            "exit_code": exit_code,
            "ended_at_s": ended_at_s,
            "duration_s": result.duration_ms / 1000,
            "stdout_ref": stdout_ref,
            "stderr_ref": stderr_ref,
            "output_ref": output_ref,
            "error": error,
            "runtime": result.runtime,
            "runtime_run_id": result.run_id,
            "session_id": result.session_id,
            "resolved_model": result.resolved_model,
            "cost_usd": float(result.cost_usd),
            "token_usage": dict(result.token_usage),
            "result_hash": result.result_hash,
        },
    ]
    transcript_ref = _write_worker_file(
        cwd_path,
        worker_dir / "transcript.jsonl",
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in transcript_events),
    )
    log_ref = _write_worker_file(
        cwd_path,
        worker_dir / "worker.log",
        "\n".join([
            f"task_id={spec.task_id}",
            f"worker_id={spec.worker_id}",
            f"role={spec.role}",
            f"status={status}",
            f"exit_code={exit_code}",
            f"error={error or ''}",
            "",
        ]),
    )
    runtime_ref = _write_worker_file(
        cwd_path,
        runtime_path,
        json.dumps(
            {
                "schema_version": "agentic-worker-runtime/v1",
                "task_id": spec.task_id,
                "worker_id": spec.worker_id,
                "role": spec.role,
                "pid": None,
                "status": status,
                "started_at_s": started_at_s,
                "ended_at_s": ended_at_s,
                "runtime": result.runtime,
                "runtime_run_id": result.run_id,
                "session_id": result.session_id,
                "resolved_model": result.resolved_model,
                "cost_usd": float(result.cost_usd),
                "token_usage": dict(result.token_usage),
                "result_hash": result.result_hash,
                "runtime_status": result.status,
                "timeout_s": spec.timeout_s,
                "budget_usd": spec.budget_usd,
                "log_ref": log_ref,
            },
            sort_keys=True,
            indent=2,
        ) + "\n",
    )

    return {
        "kind": "dynamic_subagent_result",
        "schema_version": "agentic-worker-receipt/v1",
        "task_id": spec.task_id,
        "worker_id": spec.worker_id,
        "role": spec.role,
        "persona_id": spec.persona_id,
        "status": status,
        "decision": "accept" if status == "passed" else "revise",
        "severity": "none" if status == "passed" else "important",
        "objections": [] if status == "passed" else [error or f"worker exited with {exit_code}"],
        "agent_runtime": result.runtime,
        "runtime": result.runtime,
        "runtime_run_id": result.run_id,
        "session_id": result.session_id,
        "resolved_model": result.resolved_model,
        "model": result.resolved_model,
        "cost_usd": float(result.cost_usd),
        "token_usage": dict(result.token_usage),
        "result_hash": result.result_hash,
        "runtime_status": result.status,
        "model_provenance": result.model_provenance,
        "cost_provenance": result.cost_provenance,
        "token_provenance": result.token_provenance,
        "agent_id": spec.agent_id or spec.worker_id,
        "permission_mode": spec.permission_mode,
        "tool_pins": list(spec.tool_pins),
        "timeout_s": spec.timeout_s,
        "budget_usd": spec.budget_usd,
        "exit_code": exit_code,
        "stdout_ref": stdout_ref,
        "stdout_sha256": _file_sha256(cwd_path / stdout_ref),
        "stderr_ref": stderr_ref,
        "stderr_sha256": _file_sha256(cwd_path / stderr_ref),
        "transcript_ref": transcript_ref,
        "transcript_sha256": _file_sha256(cwd_path / transcript_ref),
        "output_ref": output_ref,
        "output_sha256": _file_sha256(cwd_path / output_ref),
        "log_ref": log_ref,
        "log_sha256": _file_sha256(cwd_path / log_ref),
        "runtime_ref": runtime_ref,
        "runtime_sha256": _file_sha256(cwd_path / runtime_ref),
    }


def run_agentic_worker_fanout(
    specs: list[AgenticWorkerSpec],
    *,
    runtime_runner: RuntimeTaskRunner | None = None,
    runtime_factory: RuntimeFactory | None = None,
    runner: WorkerRunner | None = None,
    max_workers: int | None = None,
) -> list[dict[str, Any]]:
    """Run supervisor-owned workers concurrently and return receipts in input order."""
    if not specs:
        return []
    task_runner = _resolve_runtime_runner(
        runtime_runner=runtime_runner,
        runtime_factory=runtime_factory,
    )
    if task_runner is not None and runner is not None:
        raise ValueError(
            "choose a runtime runner/factory or the legacy subprocess runner"
        )
    if task_runner is None and runner is None:
        raise ValueError(
            "agentic worker fan-out requires a runtime runner/factory; "
            "pass runner= only for the explicit legacy fallback"
        )
    worker_count = max(1, min(len(specs), int(max_workers or len(specs))))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [
            pool.submit(
                run_agentic_worker,
                spec,
                runtime_runner=task_runner,
                runner=runner,
            )
            for spec in specs
        ]
        return [future.result() for future in futures]


def _resolve_runtime_runner(
    *,
    runtime_runner: RuntimeTaskRunner | None,
    runtime_factory: RuntimeFactory | None,
) -> RuntimeTaskRunner | None:
    if runtime_runner is not None and runtime_factory is not None:
        raise ValueError("provide runtime_runner or runtime_factory, not both")
    if runtime_runner is not None:
        return runtime_runner
    if runtime_factory is not None:
        return runtime_task_runner(runtime_factory)
    return None


def _worker_agent_task(
    spec: AgenticWorkerSpec,
    *,
    cwd: Path,
) -> AgentTask:
    metadata = {
        **dict(spec.runtime_metadata),
        "agentic_execution": {
            "kind": "worker",
            "task_id": spec.task_id,
            "worker_id": spec.worker_id,
            "role": spec.role,
        },
        "worker_id": spec.worker_id,
        "worker_role": spec.role,
        "persona_id": spec.persona_id,
        "agentic_permission_mode": spec.permission_mode,
        "tool_pins": list(spec.tool_pins),
        "max_budget_usd": float(spec.budget_usd),
    }
    metadata.setdefault("permission_mode", "plan")
    metadata.setdefault("allowed_tools", ["Read", "Grep", "Glob", "Bash"])
    metadata.setdefault(
        "disallowed_tools",
        ["Edit", "Write", "MultiEdit", "NotebookEdit"],
    )
    instruction = spec.instruction.strip() or (
        f"Read-only agentic worker for task {spec.task_id}. "
        f"Role: {spec.role}."
    )
    return AgentTask(
        task_id=f"{spec.task_id}::agentic-worker::{spec.worker_id}",
        instruction=instruction,
        cwd=cwd,
        model=spec.model.strip() or "agentic-worker-model",
        timeout_s=max(0.001, float(spec.timeout_s)),
        env={
            str(key): str(value)
            for key, value in spec.runtime_env.items()
        },
        inherit_env=False,
        metadata=metadata,
    )


def _validate_runtime_execution(
    execution: RuntimeExecution,
    *,
    task: AgentTask,
) -> None:
    if not isinstance(execution, RuntimeExecution):
        raise TypeError("runtime runner must return RuntimeExecution")
    result = execution.result
    if result.task_id != task.task_id:
        raise ValueError(
            "runtime result task_id does not match the agentic worker task"
        )
    if execution.handle.task_id != task.task_id:
        raise ValueError(
            "runtime handle task_id does not match the agentic worker task"
        )
    if result.run_id != execution.handle.run_id:
        raise ValueError("runtime result run_id does not match its handle")
    for field_name, value in (
        ("runtime", result.runtime),
        ("run_id", result.run_id),
        ("session_id", result.session_id),
        ("result_hash", result.result_hash),
    ):
        if not str(value or "").strip():
            raise ValueError(f"runtime result {field_name} must be non-empty")


def _failed_runtime_execution(
    task: AgentTask,
    *,
    exc: BaseException,
    runtime: str,
    started_at_ms: int,
    ended_at_ms: int,
) -> RuntimeExecution:
    run_id = f"failed-{uuid.uuid4().hex}"
    runtime_name = str(runtime or "").strip()
    if runtime_name in {"", "runtime"}:
        runtime_name = "runtime_runner"
    if isinstance(exc, asyncio.CancelledError):
        reason = "cancelled"
        result_status = "cancelled"
        event_kind = "run.cancelled"
        error = "runtime cancelled"
    elif isinstance(exc, TimeoutError):
        reason = "timeout"
        result_status = "failed"
        event_kind = "run.failed"
        error = f"{type(exc).__name__}: {exc}"
    else:
        reason = "runtime_exception"
        result_status = "failed"
        event_kind = "run.failed"
        error = f"{type(exc).__name__}: {exc}"
    event = RuntimeEvent(
        kind=event_kind,
        payload={
            "type": event_kind,
            "reason": reason,
            "error": error,
        },
        ts_ms=ended_at_ms,
    )
    session_id = run_id
    metadata = {
        "error": error,
        "failure_reason": reason,
        "returncode": None,
        "stderr": "",
    }
    result_payload = {
        "run_id": run_id,
        "task_id": task.task_id,
        "runtime": runtime_name,
        "session_id": session_id,
        "status": result_status,
        "output": "",
        "events": [event.to_dict()],
        "started_at_ms": started_at_ms,
        "ended_at_ms": ended_at_ms,
        "cost_usd": 0.0,
        "resolved_model": task.model,
        "token_usage": {},
        "metadata": metadata,
    }
    handle = AgentRunHandle(
        run_id=run_id,
        task_id=task.task_id,
        runtime=runtime_name,
        session_id=session_id,
        capabilities={},
    )
    result = AgentRunResult(
        run_id=run_id,
        task_id=task.task_id,
        runtime=runtime_name,
        session_id=session_id,
        status=result_status,
        output="",
        events=(event,),
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        cost_usd=0.0,
        resolved_model=task.model,
        result_hash=_json_sha256(result_payload),
        token_usage={},
        metadata=metadata,
    )
    return RuntimeExecution(handle=handle, events=(event,), result=result)


def _worker_status(result: AgentRunResult) -> str:
    status = str(result.status or "").strip().lower()
    if status in {"completed", "passed", "success", "succeeded"}:
        return "passed"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if _runtime_timed_out(result):
        return "timeout"
    return "failed"


def _agent_run_result_payload(result: AgentRunResult) -> dict[str, Any]:
    payload = result.to_dict()
    payload["metadata"] = {
        key: result.metadata[key]
        for key in ("returncode", "failure_reason")
        if key in result.metadata
    }
    return payload


def _runtime_timed_out(result: AgentRunResult) -> bool:
    if str(result.status or "").strip().lower() == "timeout":
        return True
    if _int(result.metadata.get("returncode")) == 124:
        return True
    reasons = [result.metadata.get("failure_reason")]
    reasons.extend(event.payload.get("reason") for event in result.events)
    return any(
        "timeout" in str(value or "").strip().lower()
        for value in reasons
    )


def _runtime_error(
    result: AgentRunResult,
    *,
    status: str,
    timeout_s: int = 0,
) -> str | None:
    if status == "passed":
        return None
    if status == "timeout":
        return f"timeout after {timeout_s}s"
    for value in (
        result.metadata.get("error"),
        result.metadata.get("stderr"),
        *(
            item
            for event in reversed(result.events)
            for item in (
                event.payload.get("error"),
                event.payload.get("reason"),
            )
        ),
    ):
        text = str(value or "").strip()
        if text:
            return text
    if status == "cancelled":
        return "runtime cancelled"
    return f"runtime ended with status={result.status}"


def cleanup_orphaned_agentic_workers(
    *,
    cwd: str | Path,
    task_id: str,
    workers: list[dict[str, Any]],
    now_s: int | float,
    is_pid_alive: PidProbe | None = None,
    terminate: Terminator | None = None,
) -> dict[str, Any]:
    """Terminate still-running workers that exceeded their timeout.

    Fan-out workers record their own refs; this cleanup path handles process
    records from longer-lived workers that outlast their gate.
    """
    cwd_path = Path(cwd).resolve()
    pid_alive = is_pid_alive or _pid_alive
    kill = terminate or _terminate
    cleaned: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for worker in workers:
        worker_id = str(worker.get("worker_id") or worker.get("id") or "")
        pid = _int(worker.get("pid"))
        timeout_s = _int(worker.get("timeout_s"))
        started_at_s = _float(worker.get("started_at_s"))
        budget_usd = worker.get("budget_usd")
        log_ref = str(worker.get("log_ref") or worker_log_ref(
            cwd=cwd_path,
            task_id=task_id,
            worker_id=worker_id or "unknown-worker",
        ))

        base = {
            "worker_id": worker_id,
            "pid": pid,
            "timeout_s": timeout_s,
            "budget_usd": budget_usd,
            "log_ref": log_ref,
        }
        if pid is None or timeout_s is None or started_at_s is None:
            skipped.append({**base, "reason": "missing_worker_runtime_fields"})
            continue
        if not pid_alive(pid):
            skipped.append({**base, "reason": "pid_not_alive"})
            continue
        elapsed_s = max(0.0, float(now_s) - started_at_s)
        if elapsed_s <= timeout_s:
            active.append({**base, "elapsed_s": elapsed_s})
            continue
        try:
            kill(pid, signal.SIGTERM)
            status = "terminated"
        except OSError as e:
            status = "terminate_failed"
            base["error"] = str(e)
        cleaned.append({
            **base,
            "status": status,
            "reason": "timeout_exceeded",
            "elapsed_s": elapsed_s,
        })

    unverifiable = [
        entry
        for entry in skipped
        if entry.get("reason") == "missing_worker_runtime_fields"
    ]
    status = (
        "cleanup_skipped_unverifiable_workers"
        if unverifiable and not cleaned and not active
        else "cleanup_completed"
    )
    return {
        "schema_version": "agentic-worker-cleanup/v1",
        "status": status,
        "task_id": task_id,
        "cwd": str(cwd_path),
        "cleaned": cleaned,
        "active": active,
        "skipped": skipped,
        "cleaned_count": len(cleaned),
        "active_count": len(active),
        "skipped_count": len(skipped),
    }


def discover_agentic_worker_runtime_records(
    *,
    cwd: str | Path,
    task_id: str,
) -> list[dict[str, Any]]:
    """Read persisted worker runtime metadata for a task."""
    cwd_path = Path(cwd).resolve()
    task_dir = cwd_path / ".handoff" / "agentic-workers" / _safe_segment(task_id)
    if not task_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for runtime_path in sorted(task_dir.glob("*/runtime.json")):
        try:
            payload = json.loads(runtime_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload.setdefault("worker_id", runtime_path.parent.name)
        payload.setdefault("task_id", task_id)
        payload.setdefault(
            "log_ref",
            worker_log_ref(cwd=cwd_path, task_id=task_id, worker_id=str(payload.get("worker_id") or runtime_path.parent.name)),
        )
        payload["runtime_ref"] = runtime_path.resolve().relative_to(cwd_path).as_posix()
        payload["runtime_sha256"] = _file_sha256(runtime_path)
        records.append(payload)
    return records


def cleanup_agentic_workers_for_task(
    *,
    cwd: str | Path,
    task_id: str,
    now_s: int | float,
    is_pid_alive: PidProbe | None = None,
    terminate: Terminator | None = None,
) -> dict[str, Any]:
    """Discover and clean stale persisted worker runtime records for a task."""
    return cleanup_orphaned_agentic_workers(
        cwd=cwd,
        task_id=task_id,
        workers=discover_agentic_worker_runtime_records(cwd=cwd, task_id=task_id),
        now_s=now_s,
        is_pid_alive=is_pid_alive,
        terminate=terminate,
    )


def discover_agentic_worker_receipts(
    *,
    cwd: str | Path,
    task_id: str,
) -> list[dict[str, Any]]:
    """Reconstruct replay-verifiable worker receipts from supervisor-owned files."""
    cwd_path = Path(cwd).resolve()
    task_dir = cwd_path / ".handoff" / "agentic-workers" / _safe_segment(task_id)
    if not task_dir.exists():
        return []
    receipts: list[dict[str, Any]] = []
    for output_path in sorted(task_dir.glob("*/output.json")):
        try:
            output = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(output, dict):
            continue
        worker_dir = output_path.parent
        worker_id = str(output.get("worker_id") or worker_dir.name)
        role = str(output.get("role") or worker_id)
        status = str(output.get("status") or "failed")
        agent_run_result = (
            output.get("agent_run_result")
            if isinstance(output.get("agent_run_result"), dict)
            else {}
        )
        runtime = str(
            output.get("runtime")
            or output.get("agent_runtime")
            or agent_run_result.get("runtime")
            or "runtime"
        )
        output_ref = output_path.resolve().relative_to(cwd_path).as_posix()
        transcript_ref = _existing_ref(cwd_path, worker_dir / "transcript.jsonl")
        stdout_ref = _existing_ref(cwd_path, worker_dir / "stdout.txt")
        stderr_ref = _existing_ref(cwd_path, worker_dir / "stderr.txt")
        log_ref = _existing_ref(cwd_path, worker_dir / "worker.log")
        runtime_ref = _existing_ref(cwd_path, worker_dir / "runtime.json")
        receipt = {
            "kind": "dynamic_subagent_result",
            "schema_version": "agentic-worker-receipt/v1",
            "receipt_id": f"agentic-worker-{worker_id}",
            "task_id": str(output.get("task_id") or task_id),
            "worker_id": worker_id,
            "role": role,
            "persona_id": str(output.get("persona_id") or role),
            "status": status,
            "decision": "accept" if status in {"passed", "accepted", "success"} else "revise",
            "severity": "none" if status in {"passed", "accepted", "success"} else "important",
            "objections": [] if status in {"passed", "accepted", "success"} else [str(output.get("error") or "worker did not pass")],
            "agent_runtime": runtime,
            "runtime": runtime,
            "runtime_run_id": str(
                output.get("runtime_run_id")
                or agent_run_result.get("run_id")
                or ""
            ),
            "session_id": str(
                output.get("session_id")
                or agent_run_result.get("session_id")
                or ""
            ),
            "resolved_model": str(
                output.get("resolved_model")
                or agent_run_result.get("resolved_model")
                or ""
            ),
            "model": str(
                output.get("resolved_model")
                or agent_run_result.get("resolved_model")
                or ""
            ),
            "cost_usd": output.get(
                "cost_usd",
                agent_run_result.get("cost_usd", 0.0),
            ),
            "token_usage": dict(
                output.get("token_usage")
                if isinstance(output.get("token_usage"), dict)
                else agent_run_result.get("token_usage")
                if isinstance(agent_run_result.get("token_usage"), dict)
                else {}
            ),
            "result_hash": str(
                output.get("result_hash")
                or agent_run_result.get("result_hash")
                or ""
            ),
            "runtime_status": str(
                output.get("runtime_status")
                or agent_run_result.get("status")
                or ""
            ),
            "model_provenance": str(
                output.get("model_provenance")
                or agent_run_result.get("model_provenance")
                or ""
            ),
            "cost_provenance": str(
                output.get("cost_provenance")
                or agent_run_result.get("cost_provenance")
                or ""
            ),
            "token_provenance": str(
                output.get("token_provenance")
                or agent_run_result.get("token_provenance")
                or ""
            ),
            "agent_id": str(output.get("agent_id") or worker_id),
            "permission_mode": str(output.get("permission_mode") or "readOnly"),
            "tool_pins": _list_or_default(output.get("tool_pins"), ["Read"]),
            "timeout_s": output.get("timeout_s"),
            "budget_usd": output.get("budget_usd"),
            "exit_code": output.get("exit_code"),
            "output_ref": output_ref,
            "output_sha256": _file_sha256(output_path),
        }
        for key, ref in [
            ("transcript", transcript_ref),
            ("stdout", stdout_ref),
            ("stderr", stderr_ref),
            ("log", log_ref),
            ("runtime", runtime_ref),
        ]:
            if ref:
                receipt[f"{key}_ref"] = ref
                receipt[f"{key}_sha256"] = _file_sha256(cwd_path / ref)
        receipts.append(receipt)
    return receipts


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate(pid: int, sig: int) -> None:
    os.kill(pid, sig)


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value or ""))
    return cleaned.strip(".-") or "unknown"


def _write_worker_file(cwd: Path, path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path.resolve().relative_to(cwd).as_posix()


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _existing_ref(cwd: Path, path: Path) -> str:
    if not path.is_file():
        return ""
    return path.resolve().relative_to(cwd).as_posix()


def _list_or_default(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        return items or list(default)
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or list(default)
    return list(default)
