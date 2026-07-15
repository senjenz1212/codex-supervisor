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

import psutil

from .config import Config
from .process_containment import (
    CONTAINMENT_ENV_VAR,
    containment_environment,
    new_containment_id,
    terminate_containment,
)
from .state import State
from .state_factory import build_state


PopenFactory = Callable[..., Any]
PidProbe = Callable[[int], bool]
BudgetHook = Callable[[Any], bool]
Clock = Callable[[], int | float]
Jitter = Callable[[int], int | float]
ProcessIdentityProbe = Callable[[int], tuple[int, float] | None]


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


def _process_identity(pid: int) -> tuple[int, float] | None:
    """Return (process-group id, create time), or None when the PID is absent."""
    try:
        process = psutil.Process(int(pid))
        return os.getpgid(int(pid)), float(process.create_time())
    except (
        PermissionError,
        ProcessLookupError,
        psutil.AccessDenied,
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
    ):
        return None


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
        max_cleanup_retry_attempts: int = 10,
        budget_hook: BudgetHook | None = None,
        popen: PopenFactory = subprocess.Popen,
        pid_alive: PidProbe = _pid_alive,
        process_identity_probe: ProcessIdentityProbe = _process_identity,
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
        self.max_cleanup_retry_attempts = max(1, int(max_cleanup_retry_attempts))
        self._reap_skipped_missing_containment: set[str] = set()
        self._cleanup_attempts: dict[str, int] = {}
        self.budget_hook = budget_hook or (lambda _row: True)
        self.popen = popen
        self.pid_alive = pid_alive
        self.process_identity_probe = process_identity_probe
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
            try:
                self.reap_stale_leases()
                self.run_once()
            except Exception as e:
                print(
                    f"workflow-job-dispatcher tick failed: {e!r}",
                    file=sys.stderr,
                )
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
        reaped: list[str] = []
        cleanup_retry_pending: list[str] = []
        for terminal_row in (
            self.state.list_terminal_dual_agent_workflow_jobs_pending_reap()
        ):
            if not terminal_row["worker_containment_id"]:
                terminal_job_id = str(terminal_row["job_id"])
                if terminal_job_id not in self._reap_skipped_missing_containment:
                    self._reap_skipped_missing_containment.add(terminal_job_id)
                    self._write_job_event(
                        terminal_row,
                        status="reap_skipped_missing_containment_identity",
                        recovery_point=str(
                            terminal_row["recovery_point"] or "terminal"
                        ),
                        pid=terminal_row["pid"],
                        error="worker_containment_identity_missing",
                    )
                continue
            termination = self._terminate_row_worker(terminal_row)
            if termination["safe_to_finalize"]:
                try:
                    self._record_worker_reaped(terminal_row, termination)
                except RuntimeError:
                    continue
                reaped.append(str(terminal_row["job_id"]))
            else:
                cleanup_retry_pending.append(str(terminal_row["job_id"]))
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
            lease_owner = str(row["leased_by"] or "")
            cleanup_retry_lease = lease_owner.startswith("cleanup:")
            active_reaper_lease = lease_owner.startswith("reaper:")
            if not lease_expired and (
                cleanup_retry_lease
                or active_reaper_lease
                or not pid_dead
            ):
                continue
            claimed_row = (
                self.state.claim_dual_agent_workflow_job_for_reap(
                    job_id=row["job_id"],
                    reaper_id=f"reaper:{self.dispatcher_id}",
                    lease_ttl_s=self.lease_ttl_s,
                    now=now,
                    expected_leased_by=row["leased_by"],
                    expected_lease_expires_at=row["lease_expires_at"],
                    expected_heartbeat_at=row["heartbeat_at"],
                    expected_pid=row["pid"],
                    expected_worker_pgid=row["worker_pgid"],
                    expected_worker_started_at=row["worker_started_at"],
                    expected_worker_containment_id=row[
                        "worker_containment_id"
                    ],
                )
            )
            if claimed_row is None:
                continue
            row = claimed_row
            result_path = Path(str(row["result_path"]))
            failure_error = "worker_lease_stale_or_dead"
            if result_path.exists():
                result: dict[str, Any] | None = None
                try:
                    loaded = json.loads(result_path.read_text(encoding="utf-8"))
                    result = loaded if isinstance(loaded, dict) else {"raw_result": loaded}
                except (OSError, json.JSONDecodeError) as e:
                    failure_error = f"malformed_worker_result: {e}"
                if result is not None:
                    termination = self._terminate_row_worker(row)
                    if not termination["safe_to_finalize"]:
                        deferred = self._defer_unsafe_cleanup(
                            row,
                            termination=termination,
                        )
                        if str(deferred["status"]) != "parked":
                            cleanup_retry_pending.append(str(row["job_id"]))
                        continue
                    status = str(result.get("status") or "completed")
                    try:
                        self.state.complete_dual_agent_workflow_job(
                            job_id=row["job_id"],
                            status=status,
                            terminal_outcome=result,
                            error="",
                            worker_reaped_at=int(self.now()),
                            termination={
                                **termination,
                                "dispatcher_id": self.dispatcher_id,
                            },
                        )
                        completed.append(row["job_id"])
                        continue
                    except ValueError as e:
                        failure_error = f"malformed_worker_result: {e}"
            failed_row = self._fail_spawned(
                row,
                error=failure_error,
            )
            if failed_row["terminal_outcome_json"] is None:
                if str(failed_row["leased_by"] or "").startswith("cleanup:"):
                    cleanup_retry_pending.append(str(row["job_id"]))
                continue
            failed.append(row["job_id"])
        return {
            "reclaimed": reclaimed,
            "failed": failed,
            "completed": completed,
            "reaped": reaped,
            "cleanup_retry_pending": cleanup_retry_pending,
        }

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
            containment_id = new_containment_id()
            with log_path.open("ab") as log_file:
                process = self.popen(
                    command,
                    cwd=str(row["cwd"]),
                    env=containment_environment(
                        os.environ,
                        containment_id,
                    ),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as e:
            return self._schedule_retry_or_park(row, error=str(e))

        identity = self.process_identity_probe(int(process.pid))
        if identity is None:
            termination = self._terminate_process(
                process,
                expected_pgid=int(process.pid),
                expected_containment_id=containment_id,
            )
            if not termination["safe_to_finalize"]:
                self.state.update_dual_agent_workflow_job(
                    job_id=row["job_id"],
                    status="running",
                    pid=int(process.pid),
                    worker_pgid=int(process.pid),
                    worker_containment_id=containment_id,
                    recovery_point="spawned",
                    error=str(termination["status"]),
                )
                refreshed = self.state.get_dual_agent_workflow_job(
                    job_id=row["job_id"]
                )
                return self._defer_unsafe_cleanup(
                    refreshed or row,
                    termination=termination,
                )
            reaped = self._complete_from_result_file(row, result_path=result_path)
            if reaped is not None:
                return reaped
            return self._schedule_retry_or_park(
                row,
                error="spawned_worker_identity_unavailable",
            )
        worker_pgid, worker_started_at = identity
        try:
            self.state.upsert_dual_agent_workflow_job(
                job_id=row["job_id"],
                run_id=row["run_id"],
                task_id=row["task_id"],
                cwd=str(row["cwd"]),
                status="running",
                pid=int(process.pid),
                worker_pgid=int(worker_pgid),
                worker_started_at=float(worker_started_at),
                worker_containment_id=containment_id,
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
            self._terminate_process(
                process,
                expected_pgid=int(worker_pgid),
                expected_started_at=float(worker_started_at),
                expected_containment_id=containment_id,
            )
            return self._park(row, reason=f"failed_to_persist_spawned_worker: {e}")

        self._write_job_event(
            row,
            status="running",
            recovery_point="spawned",
            pid=int(process.pid),
            worker_pgid=int(worker_pgid),
            worker_started_at=float(worker_started_at),
            worker_containment_id=containment_id,
        )
        refreshed = self.state.get_dual_agent_workflow_job(job_id=row["job_id"])
        return refreshed or row

    def _complete_from_result_file(
        self,
        row: Any,
        *,
        result_path: Path,
    ) -> Any | None:
        if not result_path.exists():
            return None
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        result = loaded if isinstance(loaded, dict) else {"raw_result": loaded}
        status = str(result.get("status") or "completed")
        try:
            self.state.complete_dual_agent_workflow_job(
                job_id=row["job_id"],
                status=status,
                terminal_outcome=result,
                error="",
            )
        except (ValueError, RuntimeError):
            return None
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
        termination = self._terminate_row_worker(row)
        if not termination["safe_to_finalize"]:
            return self._defer_unsafe_cleanup(
                row,
                termination=termination,
            )
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
            worker_reaped_at=int(self.now()),
            termination={
                **termination,
                "dispatcher_id": self.dispatcher_id,
            },
        )
        self._write_job_event(row, status="failed", recovery_point="terminal", error=error)
        refreshed = self.state.get_dual_agent_workflow_job(job_id=row["job_id"])
        return refreshed or row

    def _defer_unsafe_cleanup(
        self,
        row: Any,
        *,
        termination: dict[str, Any],
    ) -> Any:
        reason = str(termination.get("status") or "worker_cleanup_unsafe")
        if not row["worker_containment_id"]:
            return self._park(
                row,
                reason=f"worker_containment_identity_missing: {reason}",
            )
        job_id = str(row["job_id"])
        attempts = self._cleanup_attempts.get(job_id, 0) + 1
        if attempts > self.max_cleanup_retry_attempts:
            self._cleanup_attempts.pop(job_id, None)
            return self._park(
                row,
                reason=f"cleanup_retry_attempts_exhausted: {reason}",
            )
        self._cleanup_attempts[job_id] = attempts
        now = int(self.now())
        delay = min(self.max_backoff_s, self.base_backoff_s)
        retry_delay = min(
            self.max_backoff_s,
            delay + self.jitter(delay),
        )
        self.state.update_dual_agent_workflow_job(
            job_id=row["job_id"],
            status="running",
            error=reason,
            leased_by=f"cleanup:{self.dispatcher_id}",
            lease_expires_at=now + int(retry_delay),
            heartbeat_at=now,
        )
        self._write_job_event(
            row,
            status="cleanup_retry_pending",
            recovery_point=str(row["recovery_point"] or "spawned"),
            pid=row["pid"],
            worker_pgid=row["worker_pgid"],
            worker_started_at=row["worker_started_at"],
            worker_containment_id=row["worker_containment_id"],
            error=reason,
        )
        refreshed = self.state.get_dual_agent_workflow_job(
            job_id=row["job_id"]
        )
        return refreshed or row

    def _park(self, row: Any, *, reason: str) -> Any:
        parked = self.state.park_dual_agent_workflow_job(job_id=row["job_id"], reason=reason)
        self._write_job_event(row, status="parked", recovery_point=row["recovery_point"], error=reason)
        return parked or row

    def _row_result(self, row: Any) -> dict[str, Any]:
        status = str(row["status"])
        if status == "running" and str(row["recovery_point"]) == "spawned":
            result_status = (
                "cleanup_retry_pending"
                if str(row["leased_by"] or "").startswith("cleanup:")
                else "spawned"
            )
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
            "worker_pgid": row["worker_pgid"],
            "worker_started_at": row["worker_started_at"],
            "worker_containment_id": row["worker_containment_id"],
            "worker_reaped_at": row["worker_reaped_at"],
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
        worker_pgid: int | None = None,
        worker_started_at: float | None = None,
        worker_containment_id: str | None = None,
        error: str | None = None,
    ) -> None:
        payload = {
            "job_id": row["job_id"],
            "task_id": row["task_id"],
            "status": status,
            "recovery_point": recovery_point,
            "pid": pid,
            "worker_pgid": worker_pgid,
            "worker_started_at": worker_started_at,
            "worker_containment_id": worker_containment_id,
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
    def _same_process_start(
        observed: float,
        expected: float,
    ) -> bool:
        return abs(float(observed) - float(expected)) <= 0.001

    def _terminate_row_worker(self, row: Any) -> dict[str, Any]:
        return self._terminate_process_group(
            row["pid"],
            expected_pgid=row["worker_pgid"],
            expected_started_at=row["worker_started_at"],
            expected_containment_id=(
                str(row["worker_containment_id"])
                if row["worker_containment_id"]
                else None
            ),
        )

    def _record_worker_reaped(
        self,
        row: Any,
        termination: dict[str, Any],
    ) -> None:
        reaped_at = int(self.now())
        self.state.record_dual_agent_workflow_worker_reaped(
            job_id=row["job_id"],
            worker_reaped_at=reaped_at,
            termination={
                **termination,
                "dispatcher_id": self.dispatcher_id,
            },
        )

    def _terminate_process_group(
        self,
        pid: Any,
        *,
        process: Any = None,
        expected_pgid: Any = None,
        expected_started_at: Any = None,
        expected_containment_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            pid = int(pid)
        except (TypeError, ValueError, OverflowError):
            return self._unsafe_termination_result(
                status="invalid_worker_pid",
                pid=0,
                pgid=None,
            )
        if pid <= 0:
            return self._unsafe_termination_result(
                status="invalid_worker_pid",
                pid=pid,
                pgid=None,
            )
        if expected_pgid is not None:
            try:
                expected_pgid = int(expected_pgid)
            except (TypeError, ValueError, OverflowError):
                return self._unsafe_termination_result(
                    status="invalid_worker_process_group",
                    pid=pid,
                    pgid=None,
                )
            if expected_pgid <= 0:
                return self._unsafe_termination_result(
                    status="invalid_worker_process_group",
                    pid=pid,
                    pgid=expected_pgid,
                )
        if expected_started_at is not None:
            try:
                expected_started_at = float(expected_started_at)
            except (TypeError, ValueError, OverflowError):
                return self._unsafe_termination_result(
                    status="invalid_worker_start_identity",
                    pid=pid,
                    pgid=expected_pgid,
                )
        try:
            observed = self.process_identity_probe(pid)
        except (
            OSError,
            psutil.AccessDenied,
            TypeError,
            ValueError,
            OverflowError,
        ):
            return self._unsafe_termination_result(
                status="worker_identity_probe_failed",
                pid=pid,
                pgid=expected_pgid,
            )
        if observed is not None:
            try:
                observed = (int(observed[0]), float(observed[1]))
            except (
                IndexError,
                TypeError,
                ValueError,
                OverflowError,
            ):
                return self._unsafe_termination_result(
                    status="worker_identity_probe_invalid",
                    pid=pid,
                    pgid=expected_pgid,
                )
        root_pid_reused = bool(
            observed is not None
            and expected_started_at is not None
            and not self._same_process_start(
                observed[1],
                expected_started_at,
            )
        )
        recorded_containment_id = (
            str(expected_containment_id).strip()
            if expected_containment_id
            else ""
        )
        if root_pid_reused and not recorded_containment_id:
            return self._unsafe_termination_result(
                status="worker_identity_mismatch_pid_reused",
                pid=pid,
                pgid=expected_pgid,
                root_pid_reused=True,
            )
        if (
            not root_pid_reused
            and observed is not None
            and expected_pgid is not None
            and int(observed[0]) != int(expected_pgid)
        ):
            return {
                "status": "worker_identity_mismatch_process_group",
                "safe_to_finalize": False,
                "pid": pid,
                "pgid": expected_pgid,
                "descendant_pids": [],
            }
        if (
            not root_pid_reused
            and observed is not None
            and expected_started_at is None
            and process is None
        ):
            if not recorded_containment_id:
                return {
                    "status": "worker_identity_missing_refused_termination",
                    "safe_to_finalize": False,
                    "pid": pid,
                    "pgid": expected_pgid,
                    "descendant_pids": [],
                }
            if self._process_containment_id(pid) != recorded_containment_id:
                root_pid_reused = True

        containment_id = (
            recorded_containment_id
            if recorded_containment_id
            else self._process_containment_id(pid)
        )
        return terminate_containment(
            root_pid=pid,
            expected_root_started_at=expected_started_at,
            expected_process_group_id=(
                expected_pgid
                if expected_pgid is not None
                else observed[0] if observed is not None else pid
            ),
            containment_id=containment_id,
            process=process,
            root_pid_reused=root_pid_reused,
        )

    @staticmethod
    def _unsafe_termination_result(
        *,
        status: str,
        pid: int,
        pgid: int | None,
        root_pid_reused: bool = False,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "safe_to_finalize": False,
            "pid": pid,
            "pgid": pgid,
            "containment_id": "",
            "descendant_pids": [],
            "surviving_pids": [],
            "scan_errors": [],
            "containment_kind": "inherited_environment_same_user",
            "root_pid_reused": bool(root_pid_reused),
        }

    def _terminate_process(
        self,
        process: Any,
        *,
        expected_pgid: int | None = None,
        expected_started_at: float | None = None,
        expected_containment_id: str | None = None,
    ) -> dict[str, Any]:
        return self._terminate_process_group(
            int(getattr(process, "pid", 0) or 0),
            process=process,
            expected_pgid=expected_pgid,
            expected_started_at=expected_started_at,
            expected_containment_id=expected_containment_id,
        )

    @staticmethod
    def _process_containment_id(pid: int) -> str:
        try:
            return str(
                psutil.Process(int(pid)).environ().get(
                    CONTAINMENT_ENV_VAR,
                    "",
                )
            )
        except (
            psutil.AccessDenied,
            psutil.NoSuchProcess,
            psutil.ZombieProcess,
        ):
            return ""


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


def load_dispatcher_env(
    *,
    secrets_path: Path,
    codex_config_path: Path,
    skip_secrets: bool = False,
    skip_codex_mcp_env: bool = False,
) -> dict[str, str]:
    """Load the same env layers the shell workflow CLI loads.

    r-2026-06-11: launchd starts this dispatcher with a near-empty
    environment, and every worker it spawns inherits it — a claude execution
    lead then hangs silently with no credentials (vela2 task B: zero stdout
    for the full 5400s timeout). The shell CLI loads secrets + codex MCP env
    before running workflows (codex_supervisor_workflow_cli.py:209-211); the
    detached dispatcher must do the same or workers see two different worlds
    depending on which lane launched them. Lazy import avoids the module
    cycle (the CLI imports this module at top level).
    """
    from mcp_tools.codex_supervisor_workflow_cli import (
        load_codex_mcp_env,
        load_secrets_env,
    )

    loaded: dict[str, str] = {}
    if not skip_codex_mcp_env:
        loaded.update(load_codex_mcp_env(codex_config_path.expanduser()))
    if not skip_secrets:
        loaded.update(load_secrets_env(secrets_path.expanduser()))
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the single-writer workflow-job dispatcher.")
    parser.add_argument("--config", default=str(Path.home() / ".codex-supervisor" / "config.yaml"))
    parser.add_argument("--dispatcher-id", default=None)
    parser.add_argument("--max-concurrent-spawns", type=int, default=4)
    parser.add_argument("--lease-ttl-s", type=int, default=60)
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="Run one reap+dispatch tick and exit.")
    parser.add_argument("--job-id", help="With --once, claim a specific workflow job instead of the oldest.")
    parser.add_argument(
        "--secrets",
        default=str(Path.home() / ".codex-supervisor" / "secrets.env"),
        help="Dotenv-style secrets file loaded before dispatching (mirrors the shell CLI).",
    )
    parser.add_argument("--no-secrets", action="store_true", help="Do not load the secrets file.")
    parser.add_argument(
        "--codex-config",
        default=str(Path.home() / ".codex" / "config.toml"),
        help="Codex config whose [mcp_servers.codex_supervisor.env] section is loaded.",
    )
    parser.add_argument("--no-codex-mcp-env", action="store_true", help="Do not load codex MCP env.")
    args = parser.parse_args(argv)

    load_dispatcher_env(
        secrets_path=Path(args.secrets),
        codex_config_path=Path(args.codex_config),
        skip_secrets=args.no_secrets,
        skip_codex_mcp_env=args.no_codex_mcp_env,
    )

    cfg = Config.load(args.config)
    state = build_state(cfg)
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
