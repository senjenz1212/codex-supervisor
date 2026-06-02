"""SQLite-backed state + an async event bus.

Ticket 02 (v0.3): adds run_snapshots, hook_requests, actions, decision_labels,
and tail_offsets tables. SQLite runs in WAL mode for concurrent reads and
crash recovery.

`register_run` is the public boundary `run_registration_api` — it writes one
immutable snapshot containing the merged scope contract (caller-supplied +
built-in never-touch baseline). Re-registering with a different scope must
NOT mutate the stored snapshot.
"""
from __future__ import annotations
import asyncio
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .target.types import ScopeContract
from .redaction import redact
from .schema_migrations import run_forward_migrations
from .trace_envelope import stamp_trace_envelope


# Built-in baseline. Always merged into the stored never_touch_patterns
# even when the caller supplies none.
BUILTIN_NEVER_TOUCH: tuple[str, ...] = (
    "**/.env*",
    "**/credentials*",
    "**/.git/config",
    "**/*.pem",
    "**/*.key",
)


def canonical_terminal_outcome_json(outcome: dict[str, Any]) -> str:
    """Canonical redacted workflow-result JSON for ledger storage/comparison."""
    return json.dumps(redact(outcome), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  rollout_path  TEXT NOT NULL,
  task          TEXT,
  scope_hints   TEXT,
  started_at    INTEGER NOT NULL,
  ended_at      INTEGER,
  status        TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS events (
  event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  ts            INTEGER NOT NULL,
  source        TEXT NOT NULL,
  kind          TEXT NOT NULL,
  payload_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_run_event ON events(run_id, event_id);

CREATE TABLE IF NOT EXISTS verdicts (
  verdict_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  event_id      INTEGER,
  phase         TEXT NOT NULL,
  layer         TEXT,
  model         TEXT NOT NULL,
  output_json   TEXT NOT NULL,
  latency_ms    INTEGER,
  mode          TEXT,
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_asks (
  ask_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  question      TEXT NOT NULL,
  options_json  TEXT NOT NULL,
  status        TEXT NOT NULL,
  answer        TEXT,
  nonce         TEXT,
  asked_at      INTEGER NOT NULL,
  answered_at   INTEGER,
  expires_at    INTEGER
);

CREATE TABLE IF NOT EXISTS run_snapshots (
  run_id              TEXT PRIMARY KEY,
  config_json         TEXT NOT NULL,
  scope_contract_json TEXT NOT NULL,
  target_kind         TEXT,
  codex_cli_version   TEXT,
  created_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS hook_requests (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT,
  hook_event    TEXT NOT NULL,
  tool_name     TEXT,
  payload_json  TEXT NOT NULL,
  response_json TEXT NOT NULL,
  latency_ms    INTEGER,
  mode          TEXT NOT NULL,
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  action_type   TEXT NOT NULL,
  requested_by  TEXT NOT NULL,
  status        TEXT NOT NULL,
  payload_json  TEXT NOT NULL,
  created_at    INTEGER NOT NULL,
  resume_requested_at INTEGER,
  completed_at  INTEGER
);

CREATE TABLE IF NOT EXISTS decision_labels (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  verdict_id    INTEGER NOT NULL,
  label         TEXT NOT NULL,
  source        TEXT NOT NULL,
  notes         TEXT,
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tail_offsets (
  path          TEXT PRIMARY KEY,
  byte_offset   INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_turns (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id           TEXT,
  message_text      TEXT NOT NULL,
  request_json      TEXT NOT NULL,
  response_text     TEXT,
  status            TEXT NOT NULL,
  model             TEXT,
  tool_outputs_json TEXT NOT NULL,
  proposed_actions_json TEXT NOT NULL,
  created_at        INTEGER NOT NULL,
  completed_at      INTEGER
);

CREATE TABLE IF NOT EXISTS supervisor_conversations (
  chat_id           TEXT PRIMARY KEY,
  claude_session_id TEXT,
  summary           TEXT NOT NULL,
  active_run_id     TEXT,
  turn_count        INTEGER NOT NULL,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS run_watches (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id           TEXT NOT NULL,
  run_id            TEXT NOT NULL,
  status            TEXT NOT NULL,
  last_event_id     INTEGER NOT NULL,
  last_notified_at  INTEGER,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL,
  UNIQUE(chat_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_run_watches_run ON run_watches(run_id, status);

CREATE TABLE IF NOT EXISTS dual_agent_workflows (
  run_id              TEXT NOT NULL,
  task_id             TEXT NOT NULL,
  cwd                 TEXT NOT NULL,
  intent              TEXT NOT NULL,
  current_gate        TEXT,
  status              TEXT NOT NULL,
  max_rounds_per_gate INTEGER NOT NULL,
  user_facing         INTEGER NOT NULL,
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  PRIMARY KEY(run_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_dual_agent_workflows_status
  ON dual_agent_workflows(status, updated_at);

CREATE TABLE IF NOT EXISTS dual_agent_workflow_steps (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         TEXT NOT NULL,
  task_id        TEXT NOT NULL,
  gate           TEXT NOT NULL,
  status         TEXT NOT NULL,
  attempt_count  INTEGER NOT NULL,
  latest_event_id INTEGER,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  UNIQUE(run_id, task_id, gate)
);
CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_steps_task
  ON dual_agent_workflow_steps(run_id, task_id, gate);

CREATE TABLE IF NOT EXISTS dual_agent_workflow_jobs (
  job_id       TEXT PRIMARY KEY,
  run_id       TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  cwd          TEXT NOT NULL,
  status       TEXT NOT NULL,
  pid          INTEGER,
  request_path TEXT NOT NULL,
  result_path  TEXT NOT NULL,
  log_path     TEXT NOT NULL,
  idempotency_token TEXT,
  terminal_status TEXT,
  terminal_outcome_json TEXT,
  terminal_outcome_recorded_at INTEGER,
  returncode   INTEGER,
  error        TEXT,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_task
  ON dual_agent_workflow_jobs(run_id, task_id, status);
"""


@dataclass
class Decision:
    kind: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)


def _merge_never_touch(supplied: tuple[str, ...]) -> tuple[str, ...]:
    """Always include the built-in never-touch baseline."""
    seen: list[str] = []
    for pat in (*supplied, *BUILTIN_NEVER_TOUCH):
        if pat not in seen:
            seen.append(pat)
    return tuple(seen)


class State:
    """Connection wrapper + decision queue. Thread-safe for the daemon's single-process use."""

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        run_forward_migrations(self._conn)
        self._conn.commit()
        self._lock = asyncio.Lock()
        self._write_lock = threading.RLock()
        self.decisions: asyncio.Queue[Decision] = asyncio.Queue()

    # --- run registration (public boundary: run_registration_api) ---
    def register_run(self, *, run_id: str, session_id: str, rollout_path: str,
                      task: str | None, scope: ScopeContract,
                      target_kind: str | None = None,
                      codex_cli_version: str | None = None,
                      config_snapshot: dict[str, Any] | None = None) -> None:
        """Register a run, writing exactly one immutable run_snapshots row.

        Idempotent: if a snapshot already exists for run_id, this is a no-op
        and the stored scope/config are NOT overwritten — that invariant is
        what makes replay deterministic.
        """
        existing = self.get_run_snapshot(run_id)
        if existing is not None:
            # Snapshot already exists. Don't touch it.
            return

        merged = ScopeContract(
            allowed_paths=scope.allowed_paths,
            related_paths=scope.related_paths,
            protected_paths=scope.protected_paths,
            never_touch_patterns=_merge_never_touch(scope.never_touch_patterns),
        )

        now = int(time.time())
        self._conn.execute(
            """INSERT OR IGNORE INTO runs(
                run_id, session_id, rollout_path, task, scope_hints,
                started_at, status)
               VALUES(?, ?, ?, ?, ?, ?, 'running')""",
            (run_id, session_id, rollout_path, task,
             json.dumps(list(merged.allowed_paths)), now),
        )
        safe_config = redact(config_snapshot or {})
        self._conn.execute(
            """INSERT INTO run_snapshots(
                run_id, config_json, scope_contract_json,
                target_kind, codex_cli_version, created_at)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (run_id,
             json.dumps(safe_config),
             json.dumps(merged.to_dict()),
             target_kind, codex_cli_version, now),
        )
        self._conn.commit()

    def get_run_snapshot(self, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM run_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()

    # --- legacy run helpers (kept for v0.2 callers; new code uses register_run) ---
    def upsert_run(self, *, run_id: str, session_id: str, rollout_path: str,
                   task: str | None, scope_hints: list[str] | None) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO runs(run_id, session_id, rollout_path, task, scope_hints, started_at, status)
               VALUES(?, ?, ?, ?, ?, ?, 'running')""",
            (run_id, session_id, rollout_path, task,
             json.dumps(scope_hints or []), int(time.time())),
        )
        self._conn.commit()

    def end_run(self, run_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE runs SET ended_at=?, status=? WHERE run_id=?",
            (int(time.time()), status, run_id),
        )
        self._conn.commit()

    def active_runs(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM runs WHERE status='running'"))

    def list_runs(self, *, limit: int = 25, include_completed: bool = True) -> list[sqlite3.Row]:
        if include_completed:
            return list(self._conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT ?",
                (limit,),
            ))
        return list(self._conn.execute(
            "SELECT * FROM runs WHERE status='running' ORDER BY started_at DESC, run_id DESC LIMIT ?",
            (limit,),
        ))

    def get_run_by_session(self, session_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM runs WHERE session_id=?", (session_id,)
        ).fetchone()

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()

    # --- events ---
    def _event_payload(self, *, run_id: str, source: str, kind: str, payload: dict) -> dict:
        return redact(stamp_trace_envelope(
            run_id=run_id,
            source=source,
            kind=kind,
            payload=payload,
        ))

    def _insert_event_unlocked(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO events(run_id, ts, source, kind, payload_json) VALUES(?, ?, ?, ?, ?)",
            (run_id, int(time.time()), source, kind, json.dumps(payload)),
        )
        return cur.lastrowid or 0

    def _set_tail_offset_unlocked(self, path: str, byte_offset: int) -> None:
        self._conn.execute(
            """INSERT INTO tail_offsets(path, byte_offset, updated_at)
               VALUES(?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET byte_offset=excluded.byte_offset,
                                                updated_at=excluded.updated_at""",
            (path, byte_offset, int(time.time())),
        )

    def write_event(self, *, run_id: str, source: str, kind: str, payload: dict) -> int:
        with self._write_lock:
            event_payload = self._event_payload(
                run_id=run_id,
                source=source,
                kind=kind,
                payload=payload,
            )
            event_id = self._insert_event_unlocked(
                run_id=run_id,
                source=source,
                kind=kind,
                payload=event_payload,
            )
            self._conn.commit()
            return event_id

    def write_event_and_tail_offset(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict,
        path: str,
        byte_offset: int,
    ) -> int:
        """Append an event and advance its rollout tail offset in one commit."""
        with self._write_lock:
            event_payload = self._event_payload(
                run_id=run_id,
                source=source,
                kind=kind,
                payload=payload,
            )
            event_id = self._insert_event_unlocked(
                run_id=run_id,
                source=source,
                kind=kind,
                payload=event_payload,
            )
            self._set_tail_offset_unlocked(path, byte_offset)
            self._conn.commit()
            return event_id

    def recent_events(self, run_id: str, n: int = 20) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM events WHERE run_id=? ORDER BY event_id DESC LIMIT ?",
            (run_id, n),
        )
        rows = list(cur)
        rows.reverse()
        return [
            {"id": r["event_id"], "ts": r["ts"], "source": r["source"],
             "kind": r["kind"], **json.loads(r["payload_json"])}
            for r in rows
        ]

    def read_events_since(
        self,
        run_id: str,
        after_event_id: int | None = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read the durable event tail after a caller-owned event_id cursor."""
        page_limit = int(limit)
        if page_limit <= 0:
            return []
        cursor = int(after_event_id or 0)
        rows = self._conn.execute(
            """SELECT event_id, ts, source, kind, payload_json
               FROM events
               WHERE run_id=? AND event_id > ?
               ORDER BY event_id ASC
               LIMIT ?""",
            (run_id, cursor, page_limit),
        ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "ts": int(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def read_dual_agent_gate_events(self, run_id: str) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """SELECT event_id, ts, kind, payload_json
               FROM events
               WHERE run_id=?
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
        ))

    def get_event(self, *, run_id: str, event_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM events WHERE run_id=? AND event_id=?",
            (run_id, int(event_id)),
        ).fetchone()

    def latest_event_id(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(event_id), 0) AS max_id FROM events WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row["max_id"] if row else 0)

    # --- watched runs ---
    def create_run_watch(
        self,
        *,
        chat_id: str,
        run_id: str,
        last_event_id: int | None = None,
        status: str = "active",
    ) -> int:
        now = int(time.time())
        start_event_id = self.latest_event_id(run_id) if last_event_id is None else int(last_event_id)
        self._conn.execute(
            """INSERT INTO run_watches(
                   chat_id, run_id, status, last_event_id,
                   last_notified_at, created_at, updated_at)
               VALUES(?, ?, ?, ?, NULL, ?, ?)
               ON CONFLICT(chat_id, run_id) DO UPDATE SET
                   status=excluded.status,
                   updated_at=excluded.updated_at""",
            (chat_id, run_id, status, start_event_id, now, now),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM run_watches WHERE chat_id=? AND run_id=?",
            (chat_id, run_id),
        ).fetchone()
        return int(row["id"] if row else 0)

    def active_run_watches(self, run_id: str) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """SELECT * FROM run_watches
               WHERE run_id=? AND status='active'
               ORDER BY id""",
            (run_id,),
        ))

    def list_run_watches(
        self,
        *,
        chat_id: str | None = None,
        run_id: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if chat_id is not None:
            clauses.append("chat_id=?")
            params.append(chat_id)
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return list(self._conn.execute(
            f"SELECT * FROM run_watches{where} ORDER BY updated_at DESC, id DESC",
            params,
        ))

    def mark_run_watch_notified(self, *, watch_id: int, event_id: int) -> None:
        self._conn.execute(
            """UPDATE run_watches
                  SET last_event_id=?, last_notified_at=?, updated_at=?
                WHERE id=?""",
            (int(event_id), int(time.time()), int(time.time()), watch_id),
        )
        self._conn.commit()

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
        self._conn.execute(
            """INSERT INTO dual_agent_workflows(
                   run_id, task_id, cwd, intent, current_gate, status,
                   max_rounds_per_gate, user_facing, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, task_id) DO UPDATE SET
                   cwd=excluded.cwd,
                   intent=excluded.intent,
                   current_gate=excluded.current_gate,
                   status=excluded.status,
                   max_rounds_per_gate=excluded.max_rounds_per_gate,
                   user_facing=excluded.user_facing,
                   updated_at=excluded.updated_at""",
            (
                run_id,
                task_id,
                cwd,
                intent,
                current_gate,
                status,
                int(max_rounds_per_gate),
                1 if user_facing else 0,
                now,
                now,
            ),
        )
        self._conn.commit()

    def update_dual_agent_workflow(
        self,
        *,
        run_id: str,
        task_id: str,
        status: str | None = None,
        current_gate: str | None = None,
    ) -> None:
        assignments = ["updated_at=?"]
        params: list[Any] = [int(time.time())]
        if status is not None:
            assignments.append("status=?")
            params.append(status)
        if current_gate is not None:
            assignments.append("current_gate=?")
            params.append(current_gate)
        params.extend([run_id, task_id])
        self._conn.execute(
            f"""UPDATE dual_agent_workflows
                   SET {", ".join(assignments)}
                 WHERE run_id=? AND task_id=?""",
            params,
        )
        self._conn.commit()

    def get_dual_agent_workflow(self, *, run_id: str, task_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT * FROM dual_agent_workflows
               WHERE run_id=? AND task_id=?""",
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
        self._conn.execute(
            """INSERT INTO dual_agent_workflow_steps(
                   run_id, task_id, gate, status, attempt_count,
                   latest_event_id, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, task_id, gate) DO UPDATE SET
                   status=excluded.status,
                   attempt_count=excluded.attempt_count,
                   latest_event_id=excluded.latest_event_id,
                   updated_at=excluded.updated_at""",
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
        self._conn.commit()

    def list_dual_agent_workflow_steps(
        self,
        *,
        run_id: str,
        task_id: str,
    ) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """SELECT * FROM dual_agent_workflow_steps
               WHERE run_id=? AND task_id=?
               ORDER BY id ASC""",
            (run_id, task_id),
        ))

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
        pid: int | None = None,
        returncode: int | None = None,
        error: str | None = None,
    ) -> None:
        now = int(time.time())
        with self._write_lock:
            self._conn.execute(
                """INSERT INTO dual_agent_workflow_jobs(
                       job_id, run_id, task_id, cwd, status, pid,
                       request_path, result_path, log_path, idempotency_token,
                       returncode, error, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       status=excluded.status,
                       pid=excluded.pid,
                       idempotency_token=COALESCE(
                           excluded.idempotency_token,
                           dual_agent_workflow_jobs.idempotency_token
                       ),
                       returncode=excluded.returncode,
                       error=excluded.error,
                       updated_at=excluded.updated_at""",
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
                    returncode,
                    error,
                    now,
                    now,
                ),
            )
            self._conn.commit()

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
    ) -> tuple[sqlite3.Row, bool]:
        """Atomically reserve a detached workflow job before launching its worker.

        The idempotency token is the deduplication boundary for submit retries.
        Callers must launch a subprocess only when this returns created=True.
        """
        now = int(time.time())
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    """SELECT * FROM dual_agent_workflow_jobs
                       WHERE idempotency_token=?""",
                    (idempotency_token,),
                ).fetchone()
                if existing is not None:
                    self._conn.commit()
                    return existing, False

                self._conn.execute(
                    """INSERT INTO dual_agent_workflow_jobs(
                           job_id, run_id, task_id, cwd, status, pid,
                           request_path, result_path, log_path,
                           idempotency_token, returncode, error,
                           created_at, updated_at)
                       VALUES(?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, ?, ?)""",
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
                        now,
                        now,
                    ),
                )
                row = self._conn.execute(
                    """SELECT * FROM dual_agent_workflow_jobs
                       WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("workflow job reservation was not persisted")
                self._conn.commit()
                return row, True
            except Exception:
                self._conn.rollback()
                raise

    def update_dual_agent_workflow_job(
        self,
        *,
        job_id: str,
        status: str | None = None,
        pid: int | None = None,
        returncode: int | None = None,
        error: str | None = None,
    ) -> None:
        assignments = ["updated_at=?"]
        params: list[Any] = [int(time.time())]
        if status is not None:
            assignments.append("status=?")
            params.append(status)
        if pid is not None:
            assignments.append("pid=?")
            params.append(pid)
        if returncode is not None:
            assignments.append("returncode=?")
            params.append(returncode)
        if error is not None:
            assignments.append("error=?")
            params.append(error)
        params.append(job_id)
        with self._write_lock:
            self._conn.execute(
                f"""UPDATE dual_agent_workflow_jobs
                       SET {", ".join(assignments)}
                     WHERE job_id=?""",
                params,
            )
            self._conn.commit()

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
        """Atomically persist a detached workflow job's terminal status/outcome."""
        if not isinstance(terminal_outcome, dict) or not terminal_outcome:
            raise ValueError("terminal_outcome must be a non-empty dict")
        now = int(time.time())
        terminal_status_value = str(terminal_status or terminal_outcome.get("status") or status)
        outcome_json = canonical_terminal_outcome_json(terminal_outcome)
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """SELECT run_id, task_id, result_path FROM dual_agent_workflow_jobs
                       WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"workflow job not found: {job_id}")
                self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status=?,
                              terminal_status=?,
                              terminal_outcome_json=?,
                              terminal_outcome_recorded_at=?,
                              returncode=?,
                              error=?,
                              updated_at=?
                        WHERE job_id=?""",
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
                event_payload = self._event_payload(
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
                event_id = self._insert_event_unlocked(
                    run_id=row["run_id"],
                    source="dual_agent",
                    kind="dual_agent_workflow_terminal_outcome",
                    payload=event_payload,
                )
                self._conn.commit()
                return event_id
            except Exception:
                self._conn.rollback()
                raise

    def get_dual_agent_workflow_job(self, *, job_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT * FROM dual_agent_workflow_jobs
               WHERE job_id=?""",
            (job_id,),
        ).fetchone()

    # --- verdicts ---
    def write_verdict(self, *, run_id: str, phase: str, layer: str | None,
                      model: str, output: dict, latency_ms: int,
                      mode: str | None = None,
                      event_id: int | None = None) -> int:
        safe = redact(output)
        cur = self._conn.execute(
            """INSERT INTO verdicts(run_id, event_id, phase, layer, model, output_json, latency_ms, mode, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, event_id, phase, layer, model, json.dumps(safe),
             latency_ms, mode, int(time.time())),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    # --- hook_requests ---
    def write_hook_request(self, *, run_id: str | None, hook_event: str,
                            tool_name: str | None, payload: dict,
                            response: dict, latency_ms: int, mode: str) -> int:
        safe_payload = redact(payload)
        safe_response = redact(response)
        cur = self._conn.execute(
            """INSERT INTO hook_requests
               (run_id, hook_event, tool_name, payload_json, response_json,
                latency_ms, mode, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, hook_event, tool_name, json.dumps(safe_payload),
             json.dumps(safe_response), latency_ms, mode, int(time.time())),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    # --- actions ledger ---
    def record_action(self, *, run_id: str, action_type: str,
                       requested_by: str, payload: dict,
                       status: str = "pending") -> int:
        safe = redact(payload)
        cur = self._conn.execute(
            """INSERT INTO actions(run_id, action_type, requested_by, status, payload_json, created_at)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (run_id, action_type, requested_by, status,
             json.dumps(safe), int(time.time())),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def complete_action(self, action_id: int, status: str,
                         payload_update: dict | None = None) -> None:
        if payload_update is not None:
            row = self._conn.execute(
                "SELECT payload_json FROM actions WHERE id=?", (action_id,)
            ).fetchone()
            existing = json.loads(row["payload_json"]) if row else {}
            existing.update(redact(payload_update))
            self._conn.execute(
                "UPDATE actions SET status=?, payload_json=?, completed_at=? WHERE id=?",
                (status, json.dumps(existing), int(time.time()), action_id),
            )
        else:
            self._conn.execute(
                "UPDATE actions SET status=?, completed_at=? WHERE id=?",
                (status, int(time.time()), action_id),
            )
        self._conn.commit()

    def mark_action_resume_requested(
        self,
        action_id: int,
        *,
        payload_update: dict | None = None,
    ) -> None:
        row = self._conn.execute(
            "SELECT payload_json FROM actions WHERE id=?", (action_id,)
        ).fetchone()
        existing = json.loads(row["payload_json"]) if row else {}
        if payload_update is not None:
            existing.update(redact(payload_update))
        now = int(time.time())
        self._conn.execute(
            """UPDATE actions
                  SET status='continue_requested',
                      payload_json=?,
                      resume_requested_at=?,
                      completed_at=NULL
                WHERE id=?""",
            (json.dumps(existing), now, action_id),
        )
        self._conn.commit()

    def claim_resume_signal(
        self,
        *,
        run_id: str,
        task_id: str,
        action_type: str = "dual_agent_gate_deadlock",
    ) -> dict[str, Any] | None:
        rows = self._conn.execute(
            """SELECT * FROM actions
               WHERE run_id=? AND action_type=? AND status='continue_requested'
               ORDER BY resume_requested_at ASC, id ASC""",
            (run_id, action_type),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if str(payload.get("task_id") or "") != task_id:
                continue
            payload["resumed_at"] = int(time.time())
            cur = self._conn.execute(
                """UPDATE actions
                      SET status='resumed',
                          payload_json=?,
                          completed_at=?
                    WHERE id=? AND status='continue_requested'""",
                (json.dumps(redact(payload)), int(time.time()), row["id"]),
            )
            if cur.rowcount:
                self._conn.commit()
                return {
                    "id": row["id"],
                    "run_id": row["run_id"],
                    "action_type": row["action_type"],
                    "status": "resumed",
                    "payload": payload,
                }
        self._conn.commit()
        return None

    def claim_retry_signal(
        self,
        *,
        run_id: str,
        task_id: str,
        action_type: str = "dual_agent_validation_failure",
    ) -> dict[str, Any] | None:
        rows = self._conn.execute(
            """SELECT * FROM actions
               WHERE run_id=? AND action_type=? AND status='retry_requested'
               ORDER BY completed_at ASC, id ASC""",
            (run_id, action_type),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if str(payload.get("task_id") or "") != task_id:
                continue
            payload["retried_at"] = int(time.time())
            cur = self._conn.execute(
                """UPDATE actions
                      SET status='retried',
                          payload_json=?,
                          completed_at=?
                    WHERE id=? AND status='retry_requested'""",
                (json.dumps(redact(payload)), int(time.time()), row["id"]),
            )
            if cur.rowcount:
                self._conn.commit()
                return {
                    "id": row["id"],
                    "run_id": row["run_id"],
                    "action_type": row["action_type"],
                    "status": "retried",
                    "payload": payload,
                }
        self._conn.commit()
        return None

    def stale_paused_dual_agent_actions(
        self,
        *,
        older_than_s: int,
        now: int | None = None,
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        current = int(now if now is not None else time.time())
        cutoff = current - older_than_s
        rows = self._conn.execute(
            """SELECT * FROM actions
               WHERE action_type IN (
                   'dual_agent_gate_deadlock',
                   'dual_agent_validation_failure'
               )
                 AND status='paused'
                 AND completed_at IS NOT NULL
                 AND completed_at <= ?
               ORDER BY completed_at ASC, id ASC
               LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        stale: list[sqlite3.Row] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if payload.get("paused_digest_sent_at") is not None:
                continue
            stale.append(row)
        return stale

    def mark_paused_digest_sent(
        self,
        action_id: int,
        *,
        sent_at: int | None = None,
    ) -> None:
        row = self._conn.execute(
            "SELECT payload_json FROM actions WHERE id=?", (action_id,)
        ).fetchone()
        existing = json.loads(row["payload_json"]) if row else {}
        existing["paused_digest_sent_at"] = int(
            sent_at if sent_at is not None else time.time()
        )
        self._conn.execute(
            "UPDATE actions SET payload_json=? WHERE id=?",
            (json.dumps(redact(existing)), action_id),
        )
        self._conn.commit()

    # --- decision labels ---
    def label_decision(self, *, verdict_id: int, label: str, source: str,
                        notes: str | None = None) -> None:
        self._conn.execute(
            """INSERT INTO decision_labels(verdict_id, label, source, notes, created_at)
               VALUES(?, ?, ?, ?, ?)""",
            (verdict_id, label, source, notes, int(time.time())),
        )
        self._conn.commit()

    # --- telegram asks ---
    def create_ask(self, run_id: str, question: str, options: list[str],
                    nonce: str | None = None, expires_at: int | None = None) -> int:
        cur = self._conn.execute(
            """INSERT INTO telegram_asks(run_id, question, options_json, status, nonce, asked_at, expires_at)
               VALUES(?, ?, ?, 'pending', ?, ?, ?)""",
            (run_id, question, json.dumps(options), nonce,
             int(time.time()), expires_at),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def answer_ask(self, ask_id: int, answer: str, nonce: str | None = None) -> bool:
        """Returns True if answered, False on nonce mismatch / expired."""
        row = self._conn.execute(
            "SELECT * FROM telegram_asks WHERE ask_id=?", (ask_id,)
        ).fetchone()
        if not row or row["status"] != "pending":
            return False
        if row["nonce"] and (nonce is None or row["nonce"] != nonce):
            return False
        if row["expires_at"] is not None and int(time.time()) > row["expires_at"]:
            self._conn.execute(
                "UPDATE telegram_asks SET status='expired' WHERE ask_id=?", (ask_id,))
            self._conn.commit()
            return False
        self._conn.execute(
            "UPDATE telegram_asks SET status='answered', answer=?, answered_at=? WHERE ask_id=?",
            (answer, int(time.time()), ask_id),
        )
        self._conn.commit()
        return True

    def get_ask(self, ask_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM telegram_asks WHERE ask_id=?", (ask_id,)
        ).fetchone()

    # --- tail offsets (public boundary: event_ingestion_api) ---
    def get_tail_offset(self, path: str) -> int:
        row = self._conn.execute(
            "SELECT byte_offset FROM tail_offsets WHERE path=?", (path,)
        ).fetchone()
        return row["byte_offset"] if row else 0

    def set_tail_offset(self, path: str, byte_offset: int) -> None:
        with self._write_lock:
            self._set_tail_offset_unlocked(path, byte_offset)
            self._conn.commit()

    # --- supervisor Telegram turns ---
    def record_supervisor_turn(self, *, chat_id: str | None, message_text: str,
                               request: dict | None = None,
                               model: str | None = None) -> int:
        safe_message = redact(message_text)
        safe_request = redact(request or {})
        cur = self._conn.execute(
            """INSERT INTO supervisor_turns(
                chat_id, message_text, request_json, response_text, status,
                model, tool_outputs_json, proposed_actions_json, created_at)
               VALUES(?, ?, ?, NULL, 'running', ?, '[]', '[]', ?)""",
            (chat_id, safe_message, json.dumps(safe_request), model, int(time.time())),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def record_supervisor_notification(
        self,
        *,
        chat_id: str | None,
        response_text: str,
        request: dict | None = None,
        model: str | None = None,
        message_text: str = "[supervisor notification]",
        tool_outputs: list[dict] | None = None,
        proposed_actions: list[dict] | None = None,
    ) -> int:
        """Persist an outbound supervisor notification as completed context.

        This is for messages the daemon sends proactively, such as watched-run
        progress. They are not user prompts, but they are still part of the
        Telegram conversation Sam sees and should be available to the next
        supervisor turn.
        """
        safe_request = redact(request or {})
        cur = self._conn.execute(
            """INSERT INTO supervisor_turns(
                chat_id, message_text, request_json, response_text, status,
                model, tool_outputs_json, proposed_actions_json, created_at,
                completed_at)
               VALUES(?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?)""",
            (
                chat_id,
                redact(message_text),
                json.dumps(safe_request),
                redact(response_text),
                model,
                json.dumps(redact(tool_outputs or [])),
                json.dumps(redact(proposed_actions or [])),
                int(time.time()),
                int(time.time()),
            ),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def find_supervisor_notification(
        self,
        *,
        chat_id: str,
        run_id: str,
        event_id: int,
    ) -> sqlite3.Row | None:
        rows = self._conn.execute(
            """SELECT *
                 FROM supervisor_turns
                WHERE chat_id=?
                  AND message_text='[watched run progress]'
                ORDER BY id DESC""",
            (chat_id,),
        ).fetchall()
        for row in rows:
            try:
                request = json.loads(row["request_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if request.get("run_id") == run_id and int(request.get("event_id") or 0) == int(event_id):
                return row
        return None

    def complete_supervisor_turn(self, turn_id: int, *, response_text: str,
                                 status: str, model: str | None = None,
                                 tool_outputs: list[dict] | None = None,
                                 proposed_actions: list[dict] | None = None) -> None:
        self._conn.execute(
            """UPDATE supervisor_turns
               SET response_text=?, status=?, model=?, tool_outputs_json=?,
                   proposed_actions_json=?, completed_at=?
               WHERE id=?""",
            (
                redact(response_text),
                status,
                model,
                json.dumps(redact(tool_outputs or [])),
                json.dumps(redact(proposed_actions or [])),
                int(time.time()),
                turn_id,
            ),
        )
        self._conn.commit()

    def get_supervisor_turn(self, turn_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM supervisor_turns WHERE id=?", (turn_id,)
        ).fetchone()

    def recent_supervisor_turns(
        self,
        *,
        chat_id: str | None,
        n: int = 5,
        exclude_turn_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if not chat_id:
            return []
        params: list[Any] = [chat_id]
        where = "chat_id=?"
        if exclude_turn_id is not None:
            where += " AND id<>?"
            params.append(exclude_turn_id)
        params.append(max(1, min(int(n), 50)))
        rows = self._conn.execute(
            f"""SELECT id, chat_id, message_text, request_json, response_text,
                       status, model, tool_outputs_json, proposed_actions_json,
                       created_at, completed_at
                  FROM supervisor_turns
                 WHERE {where}
              ORDER BY id DESC LIMIT ?""",
            params,
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in reversed(rows):
            out.append(redact({
                "id": row["id"],
                "chat_id": row["chat_id"],
                "message_text": row["message_text"],
                "request": json.loads(row["request_json"] or "{}"),
                "response_text": row["response_text"],
                "status": row["status"],
                "model": row["model"],
                "tool_outputs": json.loads(row["tool_outputs_json"] or "[]"),
                "proposed_actions": json.loads(row["proposed_actions_json"] or "[]"),
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }))
        return out

    def get_supervisor_conversation(self, chat_id: str | None) -> sqlite3.Row | None:
        if not chat_id:
            return None
        return self._conn.execute(
            "SELECT * FROM supervisor_conversations WHERE chat_id=?", (chat_id,)
        ).fetchone()

    def upsert_supervisor_conversation(
        self,
        *,
        chat_id: str,
        claude_session_id: str | None = None,
        summary: str | None = None,
        active_run_id: str | None = None,
        increment_turn_count: bool = False,
    ) -> None:
        now = int(time.time())
        existing = self.get_supervisor_conversation(chat_id)
        if existing is None:
            self._conn.execute(
                """INSERT INTO supervisor_conversations(
                       chat_id, claude_session_id, summary, active_run_id,
                       turn_count, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (
                    chat_id,
                    claude_session_id,
                    redact(summary or ""),
                    active_run_id,
                    self._supervisor_turn_count(chat_id) if increment_turn_count else 0,
                    now,
                    now,
                ),
            )
        else:
            next_session = (
                claude_session_id
                if claude_session_id is not None
                else existing["claude_session_id"]
            )
            next_summary = (
                redact(summary)
                if summary is not None
                else existing["summary"]
            )
            next_active_run = (
                active_run_id
                if active_run_id is not None
                else existing["active_run_id"]
            )
            next_count = int(existing["turn_count"] or 0) + (
                1 if increment_turn_count else 0
            )
            if increment_turn_count:
                next_count = max(next_count, self._supervisor_turn_count(chat_id))
            self._conn.execute(
                """UPDATE supervisor_conversations
                      SET claude_session_id=?, summary=?, active_run_id=?,
                          turn_count=?, updated_at=?
                    WHERE chat_id=?""",
                (
                    next_session,
                    next_summary,
                    next_active_run,
                    next_count,
                    now,
                    chat_id,
                ),
            )
        self._conn.commit()

    def _supervisor_turn_count(self, chat_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM supervisor_turns WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        return int(row["c"] if row else 0)

    # --- introspection (for tests) ---
    def journal_mode(self) -> str:
        row = self._conn.execute("PRAGMA journal_mode").fetchone()
        return (row[0] if row else "").lower()

    # --- decisions queue ---
    async def enqueue_decision(self, d: Decision) -> None:
        await self.decisions.put(d)

    async def next_decision(self) -> Decision:
        return await self.decisions.get()
