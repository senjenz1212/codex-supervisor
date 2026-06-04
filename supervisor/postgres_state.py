"""Postgres-backed event/job lane for multi-writer supervisor deployments."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

from .redaction import redact
from .state import TERMINAL_WORKFLOW_JOB_STATUSES, canonical_terminal_outcome_json
from .trace_envelope import stamp_trace_envelope


POSTGRES_LOCK_ORDER = "priority ASC, created_at ASC, id ASC"

POSTGRES_CLAIM_AVAILABLE_JOBS_SQL = f"""
WITH c AS MATERIALIZED (
    SELECT id
      FROM dual_agent_workflow_jobs
     WHERE recovery_point IN ('reserved', 'request_written')
       AND status NOT IN ('parked', 'accepted', 'blocked', 'cancelled', 'completed', 'denied', 'failed')
       AND terminal_outcome_json IS NULL
       AND pid IS NULL
       AND (next_dispatch_at IS NULL OR next_dispatch_at <= %(now)s)
       AND (
             leased_by IS NULL
          OR lease_expires_at IS NULL
          OR lease_expires_at <= %(now)s
       )
       AND (%(job_id)s::text IS NULL OR job_id = %(job_id)s)
     ORDER BY {POSTGRES_LOCK_ORDER}
     LIMIT %(limit)s
     FOR UPDATE SKIP LOCKED
)
UPDATE dual_agent_workflow_jobs AS j
   SET leased_by = %(dispatcher_id)s,
       lease_expires_at = %(lease_expires_at)s,
       heartbeat_at = %(now)s,
       updated_at = %(now)s
  FROM c
 WHERE j.id = c.id
 RETURNING j.*
"""

POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_stream_sequences (
  run_id TEXT PRIMARY KEY,
  last_event_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  global_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL,
  event_id BIGINT NOT NULL,
  previous_event_id BIGINT,
  ts BIGINT NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  CONSTRAINT events_run_event_unique UNIQUE(run_id, event_id),
  CONSTRAINT events_previous_id_shape CHECK (
       (event_id = 1 AND previous_event_id IS NULL)
    OR (event_id > 1 AND previous_event_id = event_id - 1)
  )
);
CREATE INDEX IF NOT EXISTS idx_events_run_event ON events(run_id, event_id);
CREATE INDEX IF NOT EXISTS idx_events_run_ts ON events(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_global_id ON events(global_id);

CREATE TABLE IF NOT EXISTS tail_offsets (
  path TEXT PRIMARY KEY,
  byte_offset BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS dual_agent_workflows (
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  cwd TEXT NOT NULL,
  intent TEXT NOT NULL,
  current_gate TEXT,
  status TEXT NOT NULL,
  max_rounds_per_gate INTEGER NOT NULL,
  user_facing BOOLEAN NOT NULL,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  PRIMARY KEY(run_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_dual_agent_workflows_status
  ON dual_agent_workflows(status, updated_at);

CREATE TABLE IF NOT EXISTS dual_agent_workflow_steps (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  gate TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL,
  latest_event_id BIGINT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  UNIQUE(run_id, task_id, gate)
);
CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_steps_task
  ON dual_agent_workflow_steps(run_id, task_id, gate);

CREATE TABLE IF NOT EXISTS dual_agent_workflow_jobs (
  id BIGSERIAL UNIQUE,
  job_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  cwd TEXT NOT NULL,
  status TEXT NOT NULL,
  pid INTEGER,
  request_path TEXT NOT NULL,
  result_path TEXT NOT NULL,
  log_path TEXT NOT NULL,
  idempotency_token TEXT,
  priority INTEGER NOT NULL DEFAULT 100,
  recovery_point TEXT NOT NULL DEFAULT 'reserved',
  recovery_claim_token TEXT,
  recovery_claimed_at BIGINT,
  leased_by TEXT,
  lease_expires_at BIGINT,
  heartbeat_at BIGINT,
  dispatch_attempts INTEGER NOT NULL DEFAULT 0,
  next_dispatch_at BIGINT,
  parked_reason TEXT,
  request_payload_json TEXT,
  config_path TEXT,
  terminal_status TEXT,
  terminal_outcome_json TEXT,
  terminal_outcome_recorded_at BIGINT,
  returncode INTEGER,
  error TEXT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_active_idempotency_token
  ON dual_agent_workflow_jobs(idempotency_token)
  WHERE idempotency_token IS NOT NULL AND recovery_point != 'terminal';
CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_task
  ON dual_agent_workflow_jobs(run_id, task_id, status);
CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_dispatchable
  ON dual_agent_workflow_jobs(priority, created_at, id)
  WHERE recovery_point IN ('reserved', 'request_written')
    AND terminal_outcome_json IS NULL
    AND pid IS NULL;
"""


def _load_psycopg() -> tuple[Any, Any, Any]:
    try:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            "Postgres state_db requires the optional postgres dependencies. "
            "Install with `uv sync --extra postgres` or run through the "
            "`make migrate`/Postgres lane tooling."
        ) from exc
    return psycopg, sql, (dict_row, Jsonb)


def _split_sql_script(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _event_payload(*, run_id: str, source: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return redact(stamp_trace_envelope(
        run_id=run_id,
        source=source,
        kind=kind,
        payload=payload,
    ))


def _as_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {"raw": loaded}
    return {"raw": value}


def _payload_json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class PostgresState:
    """Postgres lane implementing the event/job subset of the State interface.

    The production DSN should point at a PgBouncer transaction-pool endpoint.
    SQLite remains the default for file paths; this class is selected only for
    postgres/postgresql URLs.
    """

    def __init__(
        self,
        dsn: str,
        *,
        schema: str | None = None,
        apply_schema: bool = True,
        connect: Any | None = None,
    ) -> None:
        psycopg, sql, row_helpers = _load_psycopg()
        dict_row, Jsonb = row_helpers
        self.db_path = dsn
        self.dsn = dsn
        self.schema = schema
        self._Jsonb = Jsonb
        self._sql = sql
        self._errors = psycopg.errors
        self._conn = (connect or psycopg.connect)(dsn, row_factory=dict_row)
        self._conn.autocommit = True
        self._write_lock = threading.RLock()
        self._lock = asyncio.Lock()
        if schema:
            self._conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            self._conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        if apply_schema:
            self.apply_schema()

    def apply_schema(self) -> None:
        with self._write_lock:
            with self._conn.transaction():
                for statement in _split_sql_script(POSTGRES_SCHEMA_SQL):
                    self._conn.execute(statement)
                self._conn.execute(
                    """INSERT INTO schema_migrations(version, name, applied_at)
                       VALUES(1, 'postgres.event_job_lane', %s)
                       ON CONFLICT(version) DO NOTHING""",
                    (int(time.time()),),
                )

    def close(self) -> None:
        self._conn.close()

    # --- events ---
    def _next_stream_event_id(self, run_id: str) -> tuple[int, int | None]:
        row = self._conn.execute(
            """INSERT INTO event_stream_sequences(run_id, last_event_id)
               VALUES(%s, 1)
               ON CONFLICT(run_id) DO UPDATE
                 SET last_event_id = event_stream_sequences.last_event_id + 1
               RETURNING last_event_id""",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("failed to allocate Postgres event stream id")
        event_id = int(row["last_event_id"])
        previous_id = event_id - 1 if event_id > 1 else None
        return event_id, previous_id

    def write_event(self, *, run_id: str, source: str, kind: str, payload: dict[str, Any]) -> int:
        with self._write_lock:
            with self._conn.transaction():
                event_id, previous_id = self._next_stream_event_id(run_id)
                event_payload = _event_payload(
                    run_id=run_id,
                    source=source,
                    kind=kind,
                    payload=payload,
                )
                self._conn.execute(
                    """INSERT INTO events(
                         run_id, event_id, previous_event_id, ts, source, kind, payload_json)
                       VALUES(%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        run_id,
                        event_id,
                        previous_id,
                        int(time.time()),
                        source,
                        kind,
                        self._Jsonb(event_payload),
                    ),
                )
                return event_id

    def write_event_and_tail_offset(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict[str, Any],
        path: str,
        byte_offset: int,
    ) -> int:
        with self._write_lock:
            with self._conn.transaction():
                event_id, previous_id = self._next_stream_event_id(run_id)
                event_payload = _event_payload(
                    run_id=run_id,
                    source=source,
                    kind=kind,
                    payload=payload,
                )
                now = int(time.time())
                self._conn.execute(
                    """INSERT INTO events(
                         run_id, event_id, previous_event_id, ts, source, kind, payload_json)
                       VALUES(%s, %s, %s, %s, %s, %s, %s)""",
                    (run_id, event_id, previous_id, now, source, kind, self._Jsonb(event_payload)),
                )
                self._conn.execute(
                    """INSERT INTO tail_offsets(path, byte_offset, updated_at)
                       VALUES(%s, %s, %s)
                       ON CONFLICT(path) DO UPDATE
                         SET byte_offset=EXCLUDED.byte_offset,
                             updated_at=EXCLUDED.updated_at""",
                    (path, int(byte_offset), now),
                )
                return event_id

    def read_events_since(
        self,
        run_id: str,
        after_event_id: int | None = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        page_limit = int(limit)
        if page_limit <= 0:
            return []
        rows = self._conn.execute(
            """SELECT event_id, previous_event_id, ts, source, kind, payload_json
               FROM events
               WHERE run_id=%s AND event_id > %s
               ORDER BY event_id ASC
               LIMIT %s""",
            (run_id, int(after_event_id or 0), page_limit),
        ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "previous_event_id": (
                    None if row["previous_event_id"] is None else int(row["previous_event_id"])
                ),
                "ts": int(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                "payload": _as_payload(row["payload_json"]),
            }
            for row in rows
        ]

    def recent_events(self, run_id: str, n: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT event_id, ts, source, kind, payload_json
               FROM events
               WHERE run_id=%s
               ORDER BY event_id DESC
               LIMIT %s""",
            (run_id, int(n)),
        ).fetchall()
        rows.reverse()
        return [
            {
                "id": int(row["event_id"]),
                "ts": int(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                **_as_payload(row["payload_json"]),
            }
            for row in rows
        ]

    def read_dual_agent_gate_events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT event_id, ts, kind, payload_json
               FROM events
               WHERE run_id=%s
                 AND kind IN (
                   'dual_agent_gate_round',
                   'dual_agent_gate_result',
                   'dual_agent_planning_validation',
                   'dual_agent_skill_receipt_validation',
                   'dual_agent_agentic_worker_production',
                   'dual_agent_agentic_worker_progress',
                   'dual_agent_dynamic_workflow_receipt_validation',
                   'dual_agent_dynamic_workflow_manifest',
                   'dual_agent_dynamic_workflow_synthesis',
                   'dual_agent_reviewer_unavailable_recovery',
                   'dual_agent_workflow_job',
                   'dual_agent_workflow_terminal_outcome',
                   'dual_agent_workflow_terminal_discrepancy',
                   'dual_agent_workflow_route',
                   'dual_agent_interaction_message',
                   'independent_reviewer_adjudication',
                   'independent_reviewer_review',
                   'tri_agent_cursor_review'
                 )
               ORDER BY event_id ASC""",
            (run_id,),
        ).fetchall()
        return [
            {
                **row,
                "payload_json": _payload_json_text(row["payload_json"]),
            }
            for row in rows
        ]

    def get_event(self, *, run_id: str, event_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE run_id=%s AND event_id=%s",
            (run_id, int(event_id)),
        ).fetchone()
        if row is None:
            return None
        return {
            **row,
            "payload_json": _payload_json_text(row["payload_json"]),
        }

    def latest_event_id(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(event_id), 0) AS max_id FROM events WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        return int(row["max_id"] if row else 0)

    def get_tail_offset(self, path: str) -> int:
        row = self._conn.execute(
            "SELECT byte_offset FROM tail_offsets WHERE path=%s",
            (path,),
        ).fetchone()
        return int(row["byte_offset"]) if row else 0

    def set_tail_offset(self, path: str, byte_offset: int) -> None:
        now = int(time.time())
        with self._write_lock:
            with self._conn.transaction():
                self._conn.execute(
                    """INSERT INTO tail_offsets(path, byte_offset, updated_at)
                       VALUES(%s, %s, %s)
                       ON CONFLICT(path) DO UPDATE
                         SET byte_offset=EXCLUDED.byte_offset,
                             updated_at=EXCLUDED.updated_at""",
                    (path, int(byte_offset), now),
                )

    # --- dual-agent workflow state ---
    def upsert_dual_agent_workflow(
        self,
        *,
        run_id: str,
        task_id: str,
        cwd: str,
        intent: str,
        current_gate: str | None,
        status: str,
        max_rounds_per_gate: int,
        user_facing: bool,
    ) -> None:
        now = int(time.time())
        with self._write_lock:
            with self._conn.transaction():
                self._conn.execute(
                    """INSERT INTO dual_agent_workflows(
                         run_id, task_id, cwd, intent, current_gate, status,
                         max_rounds_per_gate, user_facing, created_at, updated_at)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(run_id, task_id) DO UPDATE SET
                         cwd=EXCLUDED.cwd,
                         intent=EXCLUDED.intent,
                         current_gate=EXCLUDED.current_gate,
                         status=EXCLUDED.status,
                         max_rounds_per_gate=EXCLUDED.max_rounds_per_gate,
                         user_facing=EXCLUDED.user_facing,
                         updated_at=EXCLUDED.updated_at""",
                    (
                        run_id,
                        task_id,
                        cwd,
                        intent,
                        current_gate,
                        status,
                        int(max_rounds_per_gate),
                        bool(user_facing),
                        now,
                        now,
                    ),
                )

    def update_dual_agent_workflow(
        self,
        *,
        run_id: str,
        task_id: str,
        status: str | None = None,
        current_gate: str | None = None,
    ) -> None:
        assignments = ["updated_at=%s"]
        params: list[Any] = [int(time.time())]
        if status is not None:
            assignments.append("status=%s")
            params.append(status)
        if current_gate is not None:
            assignments.append("current_gate=%s")
            params.append(current_gate)
        params.extend([run_id, task_id])
        with self._write_lock:
            with self._conn.transaction():
                self._conn.execute(
                    f"""UPDATE dual_agent_workflows
                           SET {", ".join(assignments)}
                         WHERE run_id=%s AND task_id=%s""",
                    params,
                )

    def get_dual_agent_workflow(self, *, run_id: str, task_id: str) -> dict[str, Any] | None:
        return self._conn.execute(
            """SELECT * FROM dual_agent_workflows
               WHERE run_id=%s AND task_id=%s""",
            (run_id, task_id),
        ).fetchone()

    def record_dual_agent_workflow_step(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: str,
        status: str,
        attempt_count: int,
        latest_event_id: int | None = None,
    ) -> None:
        now = int(time.time())
        with self._write_lock:
            with self._conn.transaction():
                self._conn.execute(
                    """INSERT INTO dual_agent_workflow_steps(
                         run_id, task_id, gate, status, attempt_count,
                         latest_event_id, created_at, updated_at)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(run_id, task_id, gate) DO UPDATE SET
                         status=EXCLUDED.status,
                         attempt_count=EXCLUDED.attempt_count,
                         latest_event_id=EXCLUDED.latest_event_id,
                         updated_at=EXCLUDED.updated_at""",
                    (
                        run_id,
                        task_id,
                        gate,
                        status,
                        int(attempt_count),
                        latest_event_id,
                        now,
                        now,
                    ),
                )

    def list_dual_agent_workflow_steps(
        self,
        *,
        run_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        return list(self._conn.execute(
            """SELECT * FROM dual_agent_workflow_steps
               WHERE run_id=%s AND task_id=%s
               ORDER BY id ASC""",
            (run_id, task_id),
        ).fetchall())

    # --- workflow jobs ---
    def reserve_dual_agent_workflow_job(
        self,
        *,
        job_id: str,
        run_id: str,
        task_id: str,
        cwd: str,
        status: str,
        request_path: str,
        result_path: str,
        log_path: str,
        idempotency_token: str,
        request_payload_json: str | None = None,
        config_path: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        now = int(time.time())
        with self._write_lock:
            try:
                with self._conn.transaction():
                    existing = self._select_workflow_job_by_token(idempotency_token)
                    if existing is not None:
                        return existing, False
                    row = self._conn.execute(
                        """INSERT INTO dual_agent_workflow_jobs(
                             job_id, run_id, task_id, cwd, status, pid,
                             request_path, result_path, log_path, idempotency_token,
                             recovery_point, request_payload_json, config_path,
                             returncode, error, created_at, updated_at)
                           VALUES(%s, %s, %s, %s, %s, NULL,
                                  %s, %s, %s, %s, 'reserved', %s, %s,
                                  NULL, NULL, %s, %s)
                           RETURNING *""",
                        (
                            job_id,
                            run_id,
                            task_id,
                            cwd,
                            status,
                            request_path,
                            result_path,
                            log_path,
                            idempotency_token,
                            request_payload_json,
                            config_path,
                            now,
                            now,
                        ),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("workflow job reservation was not persisted")
                    return row, True
            except self._errors.UniqueViolation:
                with self._conn.transaction():
                    existing = self._select_workflow_job_by_token(idempotency_token)
                    if existing is None:
                        raise RuntimeError("workflow job idempotency conflict had no visible row")
                    return existing, False

    def _select_workflow_job_by_token(self, idempotency_token: str) -> dict[str, Any] | None:
        return self._conn.execute(
            """SELECT *
               FROM dual_agent_workflow_jobs
               WHERE idempotency_token=%s
               ORDER BY CASE WHEN recovery_point != 'terminal' THEN 0 ELSE 1 END,
                        created_at ASC,
                        job_id ASC
               LIMIT 1""",
            (idempotency_token,),
        ).fetchone()

    def get_dual_agent_workflow_job(self, *, job_id: str) -> dict[str, Any] | None:
        return self._conn.execute(
            "SELECT * FROM dual_agent_workflow_jobs WHERE job_id=%s",
            (job_id,),
        ).fetchone()

    def upsert_dual_agent_workflow_job(
        self,
        *,
        job_id: str,
        run_id: str,
        task_id: str,
        cwd: str,
        status: str,
        request_path: str,
        result_path: str,
        log_path: str,
        idempotency_token: str | None = None,
        recovery_point: str | None = None,
        request_payload_json: str | None = None,
        config_path: str | None = None,
        pid: int | None = None,
        returncode: int | None = None,
        error: str | None = None,
    ) -> None:
        now = int(time.time())
        recovery_point_value = recovery_point or (
            "terminal"
            if status in TERMINAL_WORKFLOW_JOB_STATUSES
            else "spawned" if pid is not None else "reserved"
        )
        with self._write_lock:
            with self._conn.transaction():
                self._conn.execute(
                    """INSERT INTO dual_agent_workflow_jobs(
                         job_id, run_id, task_id, cwd, status, pid, request_path,
                         result_path, log_path, idempotency_token, recovery_point,
                         request_payload_json, config_path, returncode, error,
                         created_at, updated_at)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(job_id) DO UPDATE SET
                         status=EXCLUDED.status,
                         pid=EXCLUDED.pid,
                         idempotency_token=COALESCE(EXCLUDED.idempotency_token, dual_agent_workflow_jobs.idempotency_token),
                         recovery_point=EXCLUDED.recovery_point,
                         recovery_claim_token=NULL,
                         recovery_claimed_at=NULL,
                         request_payload_json=COALESCE(EXCLUDED.request_payload_json, dual_agent_workflow_jobs.request_payload_json),
                         config_path=COALESCE(EXCLUDED.config_path, dual_agent_workflow_jobs.config_path),
                         returncode=EXCLUDED.returncode,
                         error=EXCLUDED.error,
                         updated_at=EXCLUDED.updated_at""",
                    (
                        job_id,
                        run_id,
                        task_id,
                        cwd,
                        status,
                        pid,
                        request_path,
                        result_path,
                        log_path,
                        idempotency_token,
                        recovery_point_value,
                        request_payload_json,
                        config_path,
                        returncode,
                        error,
                        now,
                        now,
                    ),
                )

    def update_dual_agent_workflow_job(self, *, job_id: str, **kwargs: Any) -> None:
        now = int(time.time())
        assignments = ["updated_at=%s"]
        params: list[Any] = [now]
        clear_lease = bool(kwargs.pop("clear_lease", False))
        clear_next_dispatch_at = bool(kwargs.pop("clear_next_dispatch_at", False))
        for key in (
            "status",
            "pid",
            "returncode",
            "error",
            "recovery_point",
            "request_payload_json",
            "config_path",
            "leased_by",
            "lease_expires_at",
            "heartbeat_at",
            "dispatch_attempts",
            "next_dispatch_at",
            "parked_reason",
        ):
            if key in kwargs and kwargs[key] is not None:
                assignments.append(f"{key}=%s")
                params.append(kwargs[key])
                if key == "recovery_point":
                    assignments.append("recovery_claim_token=NULL")
                    assignments.append("recovery_claimed_at=NULL")
        if clear_lease:
            assignments.extend(["leased_by=NULL", "lease_expires_at=NULL", "heartbeat_at=NULL"])
        if clear_next_dispatch_at:
            assignments.append("next_dispatch_at=NULL")
        params.append(job_id)
        with self._write_lock:
            with self._conn.transaction():
                self._conn.execute(
                    f"""UPDATE dual_agent_workflow_jobs
                           SET {", ".join(assignments)}
                         WHERE job_id=%s""",
                    params,
                )

    def count_active_dual_agent_workflow_job_leases(self, *, now: int) -> int:
        row = self._conn.execute(
            """SELECT COUNT(*) AS count
               FROM dual_agent_workflow_jobs
               WHERE recovery_point='spawned'
                 AND status='running'
                 AND terminal_outcome_json IS NULL
                 AND leased_by IS NOT NULL
                 AND lease_expires_at IS NOT NULL
                 AND lease_expires_at > %s""",
            (int(now),),
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    def claim_dual_agent_workflow_jobs_for_dispatch(
        self,
        *,
        dispatcher_id: str,
        lease_ttl_s: int,
        now: int,
        limit: int = 1,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if int(limit) <= 0:
            return []
        lease_expires_at = int(now) + max(1, int(lease_ttl_s))
        with self._write_lock:
            with self._conn.transaction():
                rows = self._conn.execute(
                    POSTGRES_CLAIM_AVAILABLE_JOBS_SQL,
                    {
                        "now": int(now),
                        "limit": int(limit),
                        "job_id": job_id,
                        "dispatcher_id": dispatcher_id,
                        "lease_expires_at": lease_expires_at,
                    },
                ).fetchall()
                return list(rows)

    def claim_next_dual_agent_workflow_job_for_dispatch(
        self,
        *,
        dispatcher_id: str,
        lease_ttl_s: int,
        now: int,
        job_id: str | None = None,
    ) -> dict[str, Any] | None:
        rows = self.claim_dual_agent_workflow_jobs_for_dispatch(
            dispatcher_id=dispatcher_id,
            lease_ttl_s=lease_ttl_s,
            now=now,
            limit=1,
            job_id=job_id,
        )
        return rows[0] if rows else None

    def clear_dual_agent_workflow_job_lease(
        self,
        *,
        job_id: str,
        next_dispatch_at: int | None = None,
        dispatch_attempts: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        assignments = [
            "leased_by=NULL",
            "lease_expires_at=NULL",
            "heartbeat_at=NULL",
            "updated_at=%s",
        ]
        params: list[Any] = [int(time.time())]
        if next_dispatch_at is not None:
            assignments.append("next_dispatch_at=%s")
            params.append(next_dispatch_at)
        if dispatch_attempts is not None:
            assignments.append("dispatch_attempts=%s")
            params.append(dispatch_attempts)
        if error is not None:
            assignments.append("error=%s")
            params.append(error)
        params.append(job_id)
        with self._write_lock:
            with self._conn.transaction():
                return self._conn.execute(
                    f"""UPDATE dual_agent_workflow_jobs
                           SET {", ".join(assignments)}
                         WHERE job_id=%s
                         RETURNING *""",
                    params,
                ).fetchone()

    def heartbeat_dual_agent_workflow_job(
        self,
        *,
        job_id: str,
        leased_by: str,
        lease_ttl_s: int,
        now: int | None = None,
    ) -> bool:
        now_value = int(time.time()) if now is None else int(now)
        lease_expires_at = now_value + max(1, int(lease_ttl_s))
        with self._write_lock:
            with self._conn.transaction():
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET lease_expires_at=%s,
                              heartbeat_at=%s,
                              updated_at=%s
                        WHERE job_id=%s
                          AND leased_by=%s
                          AND recovery_point='spawned'
                          AND terminal_outcome_json IS NULL""",
                    (lease_expires_at, now_value, now_value, job_id, leased_by),
                )
                return cursor.rowcount == 1

    def park_dual_agent_workflow_job(self, *, job_id: str, reason: str) -> dict[str, Any] | None:
        now = int(time.time())
        with self._write_lock:
            with self._conn.transaction():
                return self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status='parked',
                              error=%s,
                              parked_reason=%s,
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              heartbeat_at=NULL,
                              recovery_claim_token=NULL,
                              recovery_claimed_at=NULL,
                              updated_at=%s
                        WHERE job_id=%s
                        RETURNING *""",
                    (reason, reason, now, job_id),
                ).fetchone()

    def list_dual_agent_workflow_job_leases(self) -> list[dict[str, Any]]:
        return list(self._conn.execute(
            """SELECT *
               FROM dual_agent_workflow_jobs
               WHERE leased_by IS NOT NULL
                 AND terminal_outcome_json IS NULL
                 AND status!='parked'
               ORDER BY updated_at ASC, job_id ASC"""
        ).fetchall())

    def claim_dual_agent_workflow_job_recovery_point(
        self,
        *,
        job_id: str,
        expected_recovery_point: str,
        claim_token: str,
        claim_ttl_s: int = 60,
    ) -> dict[str, Any] | None:
        now = int(time.time())
        stale_before = now - max(0, int(claim_ttl_s))
        with self._write_lock:
            with self._conn.transaction():
                return self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET recovery_claim_token=%s,
                              recovery_claimed_at=%s,
                              updated_at=%s
                        WHERE job_id=%s
                          AND recovery_point=%s
                          AND pid IS NULL
                          AND terminal_outcome_json IS NULL
                          AND (
                                recovery_claim_token IS NULL
                             OR recovery_claimed_at IS NULL
                             OR recovery_claimed_at <= %s
                          )
                        RETURNING *""",
                    (
                        claim_token,
                        now,
                        now,
                        job_id,
                        expected_recovery_point,
                        stale_before,
                    ),
                ).fetchone()

    def complete_dual_agent_workflow_job(
        self,
        *,
        job_id: str,
        status: str,
        terminal_outcome: dict[str, Any],
        terminal_status: str | None = None,
        returncode: int | None = None,
        error: str | None = None,
    ) -> int:
        if not isinstance(terminal_outcome, dict) or not terminal_outcome:
            raise ValueError("terminal_outcome must be a non-empty dict")
        now = int(time.time())
        terminal_status_value = str(terminal_status or terminal_outcome.get("status") or status)
        outcome_json = canonical_terminal_outcome_json(terminal_outcome)
        with self._write_lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    "SELECT run_id, task_id, result_path FROM dual_agent_workflow_jobs WHERE job_id=%s",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"workflow job not found: {job_id}")
                self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status=%s,
                              recovery_point='terminal',
                              recovery_claim_token=NULL,
                              recovery_claimed_at=NULL,
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              heartbeat_at=NULL,
                              terminal_status=%s,
                              terminal_outcome_json=%s,
                              terminal_outcome_recorded_at=%s,
                              returncode=%s,
                              error=%s,
                              updated_at=%s
                        WHERE job_id=%s""",
                    (
                        status,
                        terminal_status_value,
                        outcome_json,
                        now,
                        returncode,
                        error,
                        now,
                        job_id,
                    ),
                )
                event_id, previous_id = self._next_stream_event_id(row["run_id"])
                event_payload = _event_payload(
                    run_id=row["run_id"],
                    source="dual_agent",
                    kind="dual_agent_workflow_terminal_outcome",
                    payload={
                        "job_id": job_id,
                        "task_id": row["task_id"],
                        "status": status,
                        "terminal_status": terminal_status_value,
                        "result_path": row["result_path"],
                        "transport_recovery": "detached_cli_worker",
                    },
                )
                self._conn.execute(
                    """INSERT INTO events(
                         run_id, event_id, previous_event_id, ts, source, kind, payload_json)
                       VALUES(%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        row["run_id"],
                        event_id,
                        previous_id,
                        now,
                        "dual_agent",
                        "dual_agent_workflow_terminal_outcome",
                        self._Jsonb(event_payload),
                    ),
                )
                return event_id


__all__ = [
    "POSTGRES_CLAIM_AVAILABLE_JOBS_SQL",
    "POSTGRES_LOCK_ORDER",
    "POSTGRES_SCHEMA_SQL",
    "PostgresState",
]
