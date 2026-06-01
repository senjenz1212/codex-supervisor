"""Supervisor-owned execution and bookkeeping for agentic lead worker processes."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable


PidProbe = Callable[[int], bool]
Terminator = Callable[[int, int], None]
WorkerRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class AgenticWorkerSpec:
    task_id: str
    worker_id: str
    role: str
    command: tuple[str, ...]
    cwd: str | Path
    persona_id: str = ""
    agent_runtime: str = "claude_code"
    agent_id: str = ""
    permission_mode: str = "readOnly"
    tool_pins: tuple[str, ...] = field(default_factory=tuple)
    timeout_s: int = 600
    budget_usd: float = 0.0


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
    runner: WorkerRunner = subprocess.run,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Spawn one supervisor-owned worker and return a replay-verifiable receipt."""
    cwd_path = Path(spec.cwd).resolve()
    worker_dir = cwd_path / ".handoff" / "agentic-workers" / _safe_segment(spec.task_id) / _safe_segment(spec.worker_id)
    worker_dir.mkdir(parents=True, exist_ok=True)
    started_at_s = now()
    stdout_text = ""
    stderr_text = ""
    exit_code: int | None = None
    error: str | None = None
    status = "failed"
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
                "log_ref": worker_log_ref(cwd=cwd_path, task_id=spec.task_id, worker_id=spec.worker_id),
            },
            sort_keys=True,
            indent=2,
        ) + "\n",
    )

    try:
        completed = runner(
            list(spec.command),
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=spec.timeout_s,
            check=False,
        )
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
        exit_code = int(completed.returncode)
        status = "passed" if completed.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as e:
        stdout_text = _decode_timeout_stream(e.output)
        stderr_text = _decode_timeout_stream(e.stderr)
        error = f"timeout after {spec.timeout_s}s"
        status = "timeout"
    except OSError as e:
        error = str(e)
        status = "failed"

    ended_at_s = now()
    stdout_ref = _write_worker_file(cwd_path, worker_dir / "stdout.txt", stdout_text)
    stderr_ref = _write_worker_file(cwd_path, worker_dir / "stderr.txt", stderr_text)
    output_payload = {
        "schema_version": "agentic-worker-output/v1",
        "task_id": spec.task_id,
        "worker_id": spec.worker_id,
        "role": spec.role,
        "status": status,
        "exit_code": exit_code,
        "error": error,
        "persona_id": spec.persona_id,
        "agent_runtime": spec.agent_runtime,
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
            "command": list(spec.command),
            "started_at_s": started_at_s,
            "timeout_s": spec.timeout_s,
            "budget_usd": spec.budget_usd,
        },
        {
            "event": "worker_finished",
            "task_id": spec.task_id,
            "worker_id": spec.worker_id,
            "status": status,
            "exit_code": exit_code,
            "ended_at_s": ended_at_s,
            "duration_s": max(0.0, ended_at_s - started_at_s),
            "stdout_ref": stdout_ref,
            "stderr_ref": stderr_ref,
            "output_ref": output_ref,
            "error": error,
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
        "agent_runtime": spec.agent_runtime,
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
    runner: WorkerRunner = subprocess.run,
    max_workers: int | None = None,
) -> list[dict[str, Any]]:
    """Run supervisor-owned workers concurrently and return receipts in input order."""
    if not specs:
        return []
    worker_count = max(1, min(len(specs), int(max_workers or len(specs))))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [
            pool.submit(run_agentic_worker, spec, runner=runner)
            for spec in specs
        ]
        return [future.result() for future in futures]


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

    return {
        "schema_version": "agentic-worker-cleanup/v1",
        "status": "cleanup_completed",
        "task_id": task_id,
        "cwd": str(cwd_path),
        "cleaned": cleaned,
        "active": active,
        "skipped": skipped,
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
            "agent_runtime": str(output.get("agent_runtime") or "claude_code"),
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


def _decode_timeout_stream(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


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
