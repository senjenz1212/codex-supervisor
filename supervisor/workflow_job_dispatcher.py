"""Single-writer dispatcher for detached dual-agent workflow jobs."""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .state import State, canonical_terminal_outcome_json


PopenFactory = Callable[..., Any]
PidProbe = Callable[[int], bool]
BudgetHook = Callable[[Any], bool]
Clock = Callable[[], int | float]
Jitter = Callable[[int], int | float]


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


class WorkflowJobDispatcher:
    """Claim and spawn workflow jobs from SQLite with one dispatcher owner.

    This is intentionally a single-writer service pattern for SQLite. Layer 1
    can replace the claim primitive with a multi-claimer database queue.
    """

    def __init__(
        self,
        state: State,
        *,
        dispatcher_id: str | None = None,
        max_concurrent_spawns: int = 4,
        lease_ttl_s: int = 60,
        base_backoff_s: int = 5,
        max_backoff_s: int = 60,
        max_dispatch_attempts: int = 3,
        budget_hook: BudgetHook | None = None,
        popen: PopenFactory = subprocess.Popen,
        pid_alive: PidProbe = _pid_alive,
        now: Clock | None = None,
        jitter: Jitter | None = None,
    ) -> None:
        self.state = state
        self.dispatcher_id = dispatcher_id or f"dispatcher:{os.getpid()}"
        self.max_concurrent_spawns = max(1, int(max_concurrent_spawns))
        self.lease_ttl_s = max(1, int(lease_ttl_s))
        self.base_backoff_s = max(1, int(base_backoff_s))
        self.max_backoff_s = max(self.base_backoff_s, int(max_backoff_s))
        self.max_dispatch_attempts = max(1, int(max_dispatch_attempts))
        self.budget_hook = budget_hook or (lambda _row: True)
        self.popen = popen
        self.pid_alive = pid_alive
        self.now = now or (lambda: int(time.time()))
        self.jitter = jitter or (lambda delay: random.uniform(0, max(1, delay * 0.1)))

    def run_forever(
        self,
        *,
        interval_s: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> None:
        stop = stop_event or threading.Event()
        while not stop.is_set():
            self.reap_stale_leases()
            self.run_once()
            stop.wait(max(0.1, float(interval_s)))

    def run_once(self, *, job_id: str | None = None) -> dict[str, Any]:
        now = int(self.now())
        active = self.state.count_active_dual_agent_workflow_job_leases(now=now)
        if active >= self.max_concurrent_spawns:
            return {
                "status": "backpressure",
                "active_spawns": active,
                "max_concurrent_spawns": self.max_concurrent_spawns,
            }
        row = self.state.claim_next_dual_agent_workflow_job_for_dispatch(
            dispatcher_id=self.dispatcher_id,
            lease_ttl_s=self.lease_ttl_s,
            now=now,
            job_id=job_id,
        )
        if row is None:
            return {"status": "idle", "active_spawns": active}
        if not self.budget_hook(row):
            parked = self._park(row, reason="budget_cap_exceeded")
            return {"status": "parked", "job_id": parked["job_id"], "reason": parked["parked_reason"]}
        if str(row["recovery_point"] or "reserved") == "reserved":
            row = self._write_request(row)
            if str(row["status"]) == "parked" or row["terminal_outcome_json"]:
                return self._row_result(row)
        if str(row["recovery_point"] or "") == "request_written":
            row = self._spawn(row)
        return self._row_result(row)

    def reap_stale_leases(self) -> dict[str, list[str]]:
        now = int(self.now())
        reclaimed: list[str] = []
        failed: list[str] = []
        completed: list[str] = []
        for row in self.state.list_dual_agent_workflow_job_leases():
            recovery_point = str(row["recovery_point"] or "")
            lease_expires_at = row["lease_expires_at"]
            lease_expired = lease_expires_at is None or int(lease_expires_at) <= now
            if recovery_point in {"reserved", "request_written"}:
                if lease_expired:
                    self.state.clear_dual_agent_workflow_job_lease(job_id=row["job_id"])
                    reclaimed.append(row["job_id"])
                continue
            if recovery_point != "spawned":
                continue
            pid = row["pid"]
            pid_dead = pid is None or not self.pid_alive(int(pid))
            if not lease_expired and not pid_dead:
                continue
            result_path = Path(str(row["result_path"]))
            if result_path.exists():
                try:
                    loaded = json.loads(result_path.read_text(encoding="utf-8"))
                    result = loaded if isinstance(loaded, dict) else {"raw_result": loaded}
                    status = str(result.get("status") or "completed")
                    self.state.complete_dual_agent_workflow_job(
                        job_id=row["job_id"],
                        status=status,
                        terminal_outcome=result,
                        error="",
                    )
                    completed.append(row["job_id"])
                    continue
                except (OSError, json.JSONDecodeError):
                    pass
            self._fail_spawned(row, error="worker_lease_stale_or_dead")
            failed.append(row["job_id"])
        return {"reclaimed": reclaimed, "failed": failed, "completed": completed}

    def _write_request(self, row: Any) -> Any:
        request_path = Path(str(row["request_path"]))
        request_payload_json = row["request_payload_json"] if "request_payload_json" in row.keys() else None
        if request_payload_json:
            try:
                payload = json.loads(str(request_payload_json))
            except json.JSONDecodeError as e:
                return self._park(row, reason=f"invalid_request_payload_json: {e}")
            if not isinstance(payload, dict):
                return self._park(row, reason="invalid_request_payload_json: expected object")
            payload["job_id"] = row["job_id"]
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        elif not request_path.exists():
            return self._park(row, reason="missing_request_payload_for_reserved_job")
        self.state.update_dual_agent_workflow_job(
            job_id=row["job_id"],
            status="submitted",
            recovery_point="request_written",
        )
        self._write_job_event(row, status="submitted", recovery_point="request_written")
        refreshed = self.state.get_dual_agent_workflow_job(job_id=row["job_id"])
        return refreshed or row

    def _spawn(self, row: Any) -> Any:
        if self._has_stale_spawn_claim(row):
            return self._park(row, reason="stale_spawn_claim_without_persisted_pid")
        request_path = Path(str(row["request_path"]))
        result_path = Path(str(row["result_path"]))
        log_path = Path(str(row["log_path"]))
        config_path = row["config_path"] if "config_path" in row.keys() else None
        command = [
            sys.executable,
            "-m",
            "mcp_tools.codex_supervisor_workflow_cli",
            "--config",
            str(Path(str(config_path or "~/.codex-supervisor/config.yaml")).expanduser()),
            "--request",
            str(request_path),
            "--output",
            str(result_path),
        ]
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab") as log_file:
                process = self.popen(
                    command,
                    cwd=str(row["cwd"]),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as e:
            return self._schedule_retry_or_park(row, error=str(e))

        try:
            self.state.upsert_dual_agent_workflow_job(
                job_id=row["job_id"],
                run_id=row["run_id"],
                task_id=row["task_id"],
                cwd=str(row["cwd"]),
                status="running",
                pid=int(process.pid),
                request_path=str(request_path),
                result_path=str(result_path),
                log_path=str(log_path),
                idempotency_token=row["idempotency_token"],
                recovery_point="spawned",
            )
            worker_id = f"worker:{int(process.pid)}"
            now = int(self.now())
            self.state.update_dual_agent_workflow_job(
                job_id=row["job_id"],
                leased_by=worker_id,
                lease_expires_at=now + self.lease_ttl_s,
                heartbeat_at=now,
                clear_next_dispatch_at=True,
            )
        except Exception as e:
            self._terminate_process(process)
            return self._park(row, reason=f"failed_to_persist_spawned_worker: {e}")

        self._write_job_event(
            row,
            status="running",
            recovery_point="spawned",
            pid=int(process.pid),
        )
        refreshed = self.state.get_dual_agent_workflow_job(job_id=row["job_id"])
        return refreshed or row

    def _schedule_retry_or_park(self, row: Any, *, error: str) -> Any:
        attempts = int(row["dispatch_attempts"] or 0) + 1
        if attempts >= self.max_dispatch_attempts:
            self.state.update_dual_agent_workflow_job(
                job_id=row["job_id"],
                dispatch_attempts=attempts,
                error=error,
                clear_lease=True,
            )
            refreshed = self.state.get_dual_agent_workflow_job(job_id=row["job_id"])
            return self._park(refreshed or row, reason=f"max_dispatch_attempts_exceeded: {error}")
        delay = min(self.max_backoff_s, self.base_backoff_s * (2 ** max(0, attempts - 1)))
        jittered_delay = min(self.max_backoff_s, delay + self.jitter(delay))
        next_dispatch_at = int(self.now()) + int(jittered_delay)
        self.state.clear_dual_agent_workflow_job_lease(
            job_id=row["job_id"],
            next_dispatch_at=next_dispatch_at,
            dispatch_attempts=attempts,
            error=error,
        )
        refreshed = self.state.get_dual_agent_workflow_job(job_id=row["job_id"])
        return refreshed or row

    def _fail_spawned(self, row: Any, *, error: str) -> Any:
        result = {
            "status": "failed",
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "error": error,
        }
        self.state.complete_dual_agent_workflow_job(
            job_id=row["job_id"],
            status="failed",
            terminal_outcome=result,
            error=error,
        )
        self._write_job_event(row, status="failed", recovery_point="terminal", error=error)
        refreshed = self.state.get_dual_agent_workflow_job(job_id=row["job_id"])
        return refreshed or row

    def _park(self, row: Any, *, reason: str) -> Any:
        parked = self.state.park_dual_agent_workflow_job(job_id=row["job_id"], reason=reason)
        self._write_job_event(row, status="parked", recovery_point=row["recovery_point"], error=reason)
        return parked or row

    def _row_result(self, row: Any) -> dict[str, Any]:
        status = str(row["status"])
        if status == "running" and str(row["recovery_point"]) == "spawned":
            result_status = "spawned"
        elif status == "parked":
            result_status = "parked"
        elif row["next_dispatch_at"]:
            result_status = "retry_scheduled"
        elif row["terminal_outcome_json"]:
            result_status = str(row["terminal_status"] or row["status"])
        else:
            result_status = status
        return {
            "status": result_status,
            "job_id": row["job_id"],
            "recovery_point": row["recovery_point"],
            "pid": row["pid"],
            "leased_by": row["leased_by"],
            "lease_expires_at": row["lease_expires_at"],
            "dispatch_attempts": row["dispatch_attempts"],
            "next_dispatch_at": row["next_dispatch_at"],
            "parked_reason": row["parked_reason"],
        }

    def _write_job_event(
        self,
        row: Any,
        *,
        status: str,
        recovery_point: str,
        pid: int | None = None,
        error: str | None = None,
    ) -> None:
        payload = {
            "job_id": row["job_id"],
            "task_id": row["task_id"],
            "status": status,
            "recovery_point": recovery_point,
            "pid": pid,
            "error": error,
            "request_path": row["request_path"],
            "result_path": row["result_path"],
            "log_path": row["log_path"],
            "transport_recovery": "detached_cli_worker",
            "dispatcher_id": self.dispatcher_id,
        }
        self.state.write_event(
            run_id=row["run_id"],
            source="dual_agent",
            kind="dual_agent_workflow_job",
            payload=payload,
        )

    @staticmethod
    def _has_stale_spawn_claim(row: Any, *, claim_ttl_s: int = 60) -> bool:
        try:
            claim_token = row["recovery_claim_token"]
            claimed_at = row["recovery_claimed_at"]
        except (KeyError, IndexError):
            return False
        if not claim_token or not str(claim_token).startswith("spawn:"):
            return False
        if claimed_at is None:
            return True
        try:
            claimed_at_int = int(claimed_at)
        except (TypeError, ValueError):
            return True
        return claimed_at_int <= int(time.time()) - max(0, claim_ttl_s)

    @staticmethod
    def _terminate_process(process: Any) -> None:
        terminate = getattr(process, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:
                pass
        wait = getattr(process, "wait", None)
        if callable(wait):
            try:
                wait(timeout=2)
                return
            except Exception:
                pass
        kill = getattr(process, "kill", None)
        if callable(kill):
            try:
                kill()
            except Exception:
                pass


class WorkflowJobLeaseHeartbeat:
    """Background heartbeat for a detached workflow worker process."""

    def __init__(
        self,
        state: State,
        *,
        job_id: str,
        leased_by: str,
        lease_ttl_s: int = 60,
        interval_s: float | None = None,
    ) -> None:
        self.state = state
        self.job_id = job_id
        self.leased_by = leased_by
        self.lease_ttl_s = max(1, int(lease_ttl_s))
        self.interval_s = max(0.1, float(interval_s or max(1, self.lease_ttl_s / 3)))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"workflow-job-heartbeat-{job_id}", daemon=True)

    def start(self) -> None:
        self.state.heartbeat_dual_agent_workflow_job(
            job_id=self.job_id,
            leased_by=self.leased_by,
            lease_ttl_s=self.lease_ttl_s,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            ok = self.state.heartbeat_dual_agent_workflow_job(
                job_id=self.job_id,
                leased_by=self.leased_by,
                lease_ttl_s=self.lease_ttl_s,
            )
            if not ok:
                return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the single-writer workflow-job dispatcher.")
    parser.add_argument("--config", default=str(Path.home() / ".codex-supervisor" / "config.yaml"))
    parser.add_argument("--dispatcher-id", default=None)
    parser.add_argument("--max-concurrent-spawns", type=int, default=4)
    parser.add_argument("--lease-ttl-s", type=int, default=60)
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="Run one reap+dispatch tick and exit.")
    parser.add_argument("--job-id", help="With --once, claim a specific workflow job instead of the oldest.")
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    state = State(cfg.supervisor.state_db)
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id=args.dispatcher_id,
        max_concurrent_spawns=args.max_concurrent_spawns,
        lease_ttl_s=args.lease_ttl_s,
    )
    if args.once:
        result = {
            "reaper": dispatcher.reap_stale_leases(),
            "dispatch": dispatcher.run_once(job_id=args.job_id) if args.job_id else dispatcher.run_once(),
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    dispatcher.run_forever(interval_s=args.interval_s)
    return 0


__all__ = [
    "WorkflowJobDispatcher",
    "WorkflowJobLeaseHeartbeat",
]
