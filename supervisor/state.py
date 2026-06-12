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
from .lessons import canonical_lesson_id
from .lessons import canonical_lesson_key


# Built-in baseline. Always merged into the stored never_touch_patterns
# even when the caller supplies none.
BUILTIN_NEVER_TOUCH: tuple[str, ...] = (
    "**/.env*",
    "**/credentials*",
    "**/.git/config",
    "**/*.pem",
    "**/*.key",
)

TERMINAL_WORKFLOW_JOB_STATUSES: frozenset[str] = frozenset({
    "accepted",
    "blocked",
    "cancelled",
    "completed",
    "denied",
    "failed",
})


def is_postgres_state_dsn(value: str | Path) -> bool:
    raw = str(value).strip().lower()
    return raw.startswith(("postgres://", "postgresql://"))


def canonical_terminal_outcome_json(outcome: dict[str, Any]) -> str:
    """Canonical redacted workflow-result JSON for ledger storage/comparison."""
    return json.dumps(redact(outcome), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _workflow_job_recovery_point(
    *,
    status: str,
    pid: int | None = None,
    terminal_outcome_json: str | None = None,
) -> str:
    if terminal_outcome_json or str(status) in TERMINAL_WORKFLOW_JOB_STATUSES:
        return "terminal"
    if pid is not None:
        return "spawned"
    return "reserved"


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
  recovery_point TEXT NOT NULL DEFAULT 'reserved',
  recovery_claim_token TEXT,
  recovery_claimed_at INTEGER,
  leased_by TEXT,
  lease_expires_at INTEGER,
  heartbeat_at INTEGER,
  dispatch_attempts INTEGER NOT NULL DEFAULT 0,
  next_dispatch_at INTEGER,
  parked_reason TEXT,
  request_payload_json TEXT,
  config_path TEXT,
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

CREATE TABLE IF NOT EXISTS supervisor_lessons (
  lesson_id     TEXT PRIMARY KEY,
  task_class    TEXT NOT NULL,
  gate          TEXT NOT NULL,
  taxonomy_code TEXT NOT NULL,
  root_cause    TEXT NOT NULL,
  remediation   TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  normalized_key TEXT NOT NULL DEFAULT '',
  observed_count INTEGER NOT NULL DEFAULT 1,
  injection_count INTEGER NOT NULL DEFAULT 0,
  recurrence_count INTEGER NOT NULL DEFAULT 0,
  retired_at INTEGER,
  created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_supervisor_lessons_task_gate
  ON supervisor_lessons(task_class, gate, created_at);

CREATE TABLE IF NOT EXISTS supervisor_quality_trends (
  id                             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id                         TEXT NOT NULL,
  task_id                        TEXT NOT NULL,
  task_class                     TEXT NOT NULL,
  gate                           TEXT NOT NULL,
  accepted                       INTEGER NOT NULL,
  first_pass_accepted            INTEGER NOT NULL,
  revision_rounds                INTEGER NOT NULL,
  time_to_accepted_outcome_s     REAL,
  p11_audit_sample_size          INTEGER NOT NULL DEFAULT 0,
  false_accept_count             INTEGER NOT NULL DEFAULT 0,
  false_accept_denominator       INTEGER NOT NULL DEFAULT 0,
  false_accept_rate              REAL NOT NULL DEFAULT 0.0,
  policy_overlay_hash            TEXT NOT NULL DEFAULT '',
  policy_proposal_id             TEXT NOT NULL DEFAULT '',
  details_json                   TEXT NOT NULL DEFAULT '{}',
  computed_at                    INTEGER NOT NULL,
  UNIQUE(run_id, gate)
);
CREATE INDEX IF NOT EXISTS idx_supervisor_quality_trends_task_gate
  ON supervisor_quality_trends(task_class, gate, computed_at);

CREATE TABLE IF NOT EXISTS supervisor_autoresearch_experiments (
  experiment_id        TEXT PRIMARY KEY,
  signal_key           TEXT NOT NULL UNIQUE,
  status               TEXT NOT NULL,
  task_class           TEXT NOT NULL,
  gate                 TEXT NOT NULL,
  taxonomy_code        TEXT NOT NULL,
  experiment_json      TEXT NOT NULL,
  attempt_json         TEXT NOT NULL,
  provenance_json      TEXT NOT NULL,
  report_only_reason   TEXT NOT NULL DEFAULT '',
  proposal_pointer_json TEXT NOT NULL DEFAULT '{}',
  report_ref           TEXT NOT NULL DEFAULT '',
  report_sha256        TEXT NOT NULL DEFAULT '',
  last_run_id          TEXT NOT NULL DEFAULT '',
  last_run_started_at  INTEGER,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL,
  activated_at         INTEGER,
  activated_by         TEXT,
  activation_channel   TEXT
);
CREATE INDEX IF NOT EXISTS idx_supervisor_autoresearch_experiments_status
  ON supervisor_autoresearch_experiments(status, updated_at);
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


def _quality_trend_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key in (
        "accepted",
        "first_pass_accepted",
        "revision_rounds",
        "p11_audit_sample_size",
        "false_accept_count",
        "false_accept_denominator",
        "computed_at",
    ):
        payload[key] = int(payload.get(key) or 0)
    payload["accepted"] = bool(payload["accepted"])
    payload["first_pass_accepted"] = bool(payload["first_pass_accepted"])
    payload["false_accept_rate"] = float(payload.get("false_accept_rate") or 0.0)
    if payload.get("time_to_accepted_outcome_s") is not None:
        payload["time_to_accepted_outcome_s"] = float(payload["time_to_accepted_outcome_s"])
    try:
        payload["details"] = json.loads(str(payload.pop("details_json") or "{}"))
    except json.JSONDecodeError:
        payload["details"] = {}
    return payload


def _quality_trend_summary_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    run_count = int(row["run_count"] or 0)
    accepted_count = int(row["accepted_count"] or 0)
    first_pass_count = int(row["first_pass_accepted_count"] or 0)
    false_accept_denominator = int(row["false_accept_denominator"] or 0)
    false_accept_count = int(row["false_accept_count"] or 0)
    return {
        "task_class": row["task_class"],
        "gate": row["gate"],
        "policy_overlay_hashes": _split_group_concat(row["policy_overlay_hashes"]),
        "policy_proposal_ids": _split_group_concat(row["policy_proposal_ids"]),
        "run_count": run_count,
        "accepted_count": accepted_count,
        "acceptance_rate": (accepted_count / run_count) if run_count else 0.0,
        "first_pass_accepted_count": first_pass_count,
        "first_pass_acceptance_rate": (first_pass_count / run_count) if run_count else 0.0,
        "avg_revision_rounds": float(row["avg_revision_rounds"] or 0.0),
        "avg_time_to_accepted_outcome_s": (
            float(row["avg_time_to_accepted_outcome_s"])
            if row["avg_time_to_accepted_outcome_s"] is not None
            else None
        ),
        "p11_audit_sample_size": int(row["p11_audit_sample_size"] or 0),
        "false_accept_count": false_accept_count,
        "false_accept_denominator": false_accept_denominator,
        "false_accept_rate": (
            false_accept_count / false_accept_denominator
            if false_accept_denominator
            else 0.0
        ),
    }


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        loaded = {}
    return loaded if isinstance(loaded, dict) else {}


def _split_group_concat(value: Any) -> list[str]:
    return sorted({item for item in str(value or "").split(",") if item})


def _autoresearch_experiment_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["experiment"] = _json_payload(payload.pop("experiment_json", "{}"))
    payload["attempt"] = _json_payload(payload.pop("attempt_json", "{}"))
    payload["provenance"] = _json_payload(payload.pop("provenance_json", "{}"))
    payload["proposal_pointer"] = _json_payload(payload.pop("proposal_pointer_json", "{}"))
    for key in ("created_at", "updated_at", "activated_at", "last_run_started_at"):
        if payload.get(key) is not None:
            payload[key] = int(payload[key])
    return payload


class State:
    """Connection wrapper + decision queue. Thread-safe for the daemon's single-process use."""

    def __new__(cls, db_path: str):
        if cls is State and is_postgres_state_dsn(db_path):
            from .postgres_state import PostgresState

            return PostgresState(db_path)
        return super().__new__(cls)

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
                   'dual_agent_runtime_evidence',
                   'dual_agent_reviewer_unavailable_recovery',
                   'dual_agent_workflow_job',
                   'dual_agent_workflow_terminal_outcome',
                   'dual_agent_workflow_terminal_discrepancy',
                   'dual_agent_workflow_route',
                   'dual_agent_interaction_message',
                   'independent_reviewer_adjudication',
                   'independent_reviewer_review',
                   'no_mistakes_finding',
                   'no_mistakes_validation_completed',
                   'no_mistakes_validation_failed',
                   'no_mistakes_validation_skipped',
                   'no_mistakes_validation_started',
                   'receipt_provenance_downgraded',
                   'supervisor_lesson_injection',
                   'supervisor_lesson_recorded',
                   'supervisor_policy_overlay_snapshot',
                   'tri_agent_cursor_review'
                 )
               ORDER BY event_id ASC""",
            (run_id,),
        ))

    # --- cross-run lessons ---
    def record_supervisor_lesson(
        self,
        *,
        task_class: str,
        gate: str,
        taxonomy_code: str,
        root_cause: str,
        remediation: str,
        source_run_id: str,
        created_at: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        now = int(time.time()) if created_at is None else int(created_at)
        lesson_id = canonical_lesson_id(
            task_class=task_class,
            gate=gate,
            taxonomy_code=taxonomy_code,
            root_cause=root_cause,
            remediation=remediation,
            source_run_id=source_run_id,
        )
        normalized_key = canonical_lesson_key(
            task_class=task_class,
            gate=gate,
            taxonomy_code=taxonomy_code,
            root_cause=root_cause,
            remediation=remediation,
        )
        with self._write_lock:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO supervisor_lessons(
                     lesson_id, task_class, gate, taxonomy_code, root_cause,
                     remediation, source_run_id, normalized_key, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    lesson_id,
                    str(task_class or "general"),
                    str(gate or "unknown"),
                    str(taxonomy_code or "unknown_failure"),
                    str(root_cause or "unknown failure"),
                    str(remediation or "Verify this known failure mode before claiming completion."),
                    str(source_run_id),
                    normalized_key,
                    now,
                ),
            )
            created = cur.rowcount > 0
            if not created:
                self._conn.execute(
                    """UPDATE supervisor_lessons
                          SET observed_count=observed_count + 1
                        WHERE lesson_id=?""",
                    (lesson_id,),
                )
            row = self._conn.execute(
                "SELECT * FROM supervisor_lessons WHERE lesson_id=?",
                (lesson_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("supervisor lesson was not persisted")
            self._conn.commit()
            return dict(row), created

    def query_supervisor_lessons(
        self,
        *,
        task_class: str,
        gate: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._conn.execute(
                """SELECT * FROM supervisor_lessons
                   WHERE task_class=? AND gate=? AND retired_at IS NULL
                   ORDER BY created_at DESC, lesson_id ASC
                   LIMIT ?""",
                (str(task_class or "general"), str(gate or "unknown"), int(limit)),
            ).fetchall()
        ]

    def list_supervisor_lessons(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._conn.execute(
                """SELECT * FROM supervisor_lessons
                   ORDER BY created_at DESC, lesson_id ASC
                   LIMIT ?""",
                (int(limit),),
            ).fetchall()
        ]

    def record_supervisor_lesson_injection_feedback(
        self,
        *,
        lesson_ids: list[str] | tuple[str, ...],
        recurring_taxonomy_codes: list[str] | tuple[str, ...] = (),
        retire_after: int = 3,
        observed_at: int | None = None,
    ) -> None:
        now = int(time.time()) if observed_at is None else int(observed_at)
        recurring = {str(code) for code in recurring_taxonomy_codes}
        with self._write_lock:
            for lesson_id in lesson_ids:
                row = self._conn.execute(
                    "SELECT taxonomy_code FROM supervisor_lessons WHERE lesson_id=?",
                    (str(lesson_id),),
                ).fetchone()
                if row is None:
                    continue
                recurs = str(row["taxonomy_code"]) in recurring
                self._conn.execute(
                    """UPDATE supervisor_lessons
                          SET injection_count=injection_count + 1,
                              recurrence_count=recurrence_count + ?,
                              retired_at=CASE
                                WHEN retired_at IS NULL
                                 AND injection_count + 1 >= ?
                                 AND recurrence_count + ? >= ?
                                THEN ?
                                ELSE retired_at
                              END
                        WHERE lesson_id=?""",
                    (
                        1 if recurs else 0,
                        int(retire_after),
                        1 if recurs else 0,
                        int(retire_after),
                        now,
                        str(lesson_id),
                    ),
                )
            self._conn.commit()

    # --- quality trend metrics ---
    def upsert_quality_trend_row(
        self,
        *,
        run_id: str,
        task_id: str,
        task_class: str,
        gate: str,
        accepted: bool,
        first_pass_accepted: bool,
        revision_rounds: int,
        time_to_accepted_outcome_s: float | None,
        policy_overlay_hash: str = "",
        policy_proposal_id: str = "",
        details: dict[str, Any] | None = None,
        computed_at: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time()) if computed_at is None else int(computed_at)
        details_json = json.dumps(redact(details or {}), sort_keys=True)
        with self._write_lock:
            self._conn.execute(
                """INSERT INTO supervisor_quality_trends(
                     run_id, task_id, task_class, gate, accepted,
                     first_pass_accepted, revision_rounds,
                     time_to_accepted_outcome_s, policy_overlay_hash,
                     policy_proposal_id, details_json, computed_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id, gate) DO UPDATE SET
                     task_id=excluded.task_id,
                     task_class=excluded.task_class,
                     accepted=excluded.accepted,
                     first_pass_accepted=excluded.first_pass_accepted,
                     revision_rounds=excluded.revision_rounds,
                     time_to_accepted_outcome_s=excluded.time_to_accepted_outcome_s,
                     policy_overlay_hash=excluded.policy_overlay_hash,
                     policy_proposal_id=excluded.policy_proposal_id,
                     details_json=excluded.details_json,
                     computed_at=excluded.computed_at""",
                (
                    run_id,
                    task_id,
                    str(task_class or "unclassified"),
                    gate,
                    1 if accepted else 0,
                    1 if first_pass_accepted else 0,
                    int(revision_rounds),
                    time_to_accepted_outcome_s,
                    str(policy_overlay_hash or ""),
                    str(policy_proposal_id or ""),
                    details_json,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM supervisor_quality_trends WHERE run_id=? AND gate=?",
                (run_id, gate),
            ).fetchone()
            self._conn.commit()
            if row is None:
                raise RuntimeError("quality trend row was not persisted")
            return _quality_trend_row_to_dict(row)

    def update_quality_trend_audit(
        self,
        *,
        run_id: str,
        gate: str,
        sample_size: int,
        false_accept_count: int,
        false_accept_denominator: int,
        audit_details: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        denominator = max(0, int(false_accept_denominator))
        false_count = max(0, int(false_accept_count))
        rate = (false_count / denominator) if denominator else 0.0
        with self._write_lock:
            existing = self._conn.execute(
                "SELECT details_json FROM supervisor_quality_trends WHERE run_id=? AND gate=?",
                (run_id, gate),
            ).fetchone()
            if existing is None:
                return None
            try:
                details = json.loads(existing["details_json"] or "{}")
            except json.JSONDecodeError:
                details = {}
            details["p11_audit"] = redact(audit_details or {})
            self._conn.execute(
                """UPDATE supervisor_quality_trends
                      SET p11_audit_sample_size=?,
                          false_accept_count=?,
                          false_accept_denominator=?,
                          false_accept_rate=?,
                          details_json=?,
                          computed_at=?
                    WHERE run_id=? AND gate=?""",
                (
                    int(sample_size),
                    false_count,
                    denominator,
                    rate,
                    json.dumps(details, sort_keys=True),
                    int(time.time()),
                    run_id,
                    gate,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM supervisor_quality_trends WHERE run_id=? AND gate=?",
                (run_id, gate),
            ).fetchone()
            self._conn.commit()
            return _quality_trend_row_to_dict(row) if row is not None else None

    def query_quality_trends(
        self,
        *,
        task_class: str | None = None,
        gate: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_class:
            clauses.append("task_class=?")
            params.append(task_class)
        if gate:
            clauses.append("gate=?")
            params.append(gate)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT
                    task_class,
                    gate,
                    GROUP_CONCAT(DISTINCT NULLIF(policy_overlay_hash, '')) AS policy_overlay_hashes,
                    GROUP_CONCAT(DISTINCT NULLIF(policy_proposal_id, '')) AS policy_proposal_ids,
                    COUNT(*) AS run_count,
                    SUM(accepted) AS accepted_count,
                    SUM(first_pass_accepted) AS first_pass_accepted_count,
                    AVG(revision_rounds) AS avg_revision_rounds,
                    AVG(time_to_accepted_outcome_s) AS avg_time_to_accepted_outcome_s,
                    SUM(p11_audit_sample_size) AS p11_audit_sample_size,
                    SUM(false_accept_count) AS false_accept_count,
                    SUM(false_accept_denominator) AS false_accept_denominator
                  FROM supervisor_quality_trends
                  {where}
                  GROUP BY task_class, gate
                  ORDER BY task_class ASC, gate ASC""",
            tuple(params),
        ).fetchall()
        return [_quality_trend_summary_to_dict(row) for row in rows]

    def count_quality_trend_rows(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM supervisor_quality_trends"
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    def list_quality_trend_rows(
        self,
        *,
        task_class: str | None = None,
        gate: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_class:
            clauses.append("task_class=?")
            params.append(task_class)
        if gate:
            clauses.append("gate=?")
            params.append(gate)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT * FROM supervisor_quality_trends
                {where}
                ORDER BY computed_at ASC, run_id ASC, gate ASC""",
            tuple(params),
        ).fetchall()
        return [_quality_trend_row_to_dict(row) for row in rows]

    def list_p11_audit_candidate_run_ids(self, *, limit: int = 50) -> list[str]:
        rows = self._conn.execute(
            """SELECT DISTINCT run_id
                 FROM events
                WHERE kind='dual_agent_gate_result'
                  AND (
                    json_extract(payload_json, '$.gate') IN ('execution', 'outcome_review')
                  )
                  AND (
                    lower(COALESCE(json_extract(payload_json, '$.status'), '')) IN ('accepted', 'accept')
                    OR lower(COALESCE(json_extract(payload_json, '$.outcome.decision'), '')) IN ('accepted', 'accept')
                  )
                ORDER BY event_id DESC
                LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [str(row["run_id"]) for row in rows]

    # --- AutoResearch experiment queue ---
    def upsert_autoresearch_experiment_draft(
        self,
        *,
        experiment_id: str,
        signal_key: str,
        status: str,
        task_class: str,
        gate: str,
        taxonomy_code: str,
        experiment: dict[str, Any],
        attempt: dict[str, Any],
        provenance: dict[str, Any],
        report_only_reason: str = "",
        proposal_pointer: dict[str, Any] | None = None,
        created_at: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        now = int(time.time()) if created_at is None else int(created_at)
        with self._write_lock:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO supervisor_autoresearch_experiments(
                     experiment_id, signal_key, status, task_class, gate,
                     taxonomy_code, experiment_json, attempt_json, provenance_json,
                     report_only_reason, proposal_pointer_json, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    experiment_id,
                    signal_key,
                    status,
                    task_class,
                    gate,
                    taxonomy_code,
                    json.dumps(redact(experiment), sort_keys=True),
                    json.dumps(redact(attempt), sort_keys=True),
                    json.dumps(redact(provenance), sort_keys=True),
                    report_only_reason,
                    json.dumps(redact(proposal_pointer or {}), sort_keys=True),
                    now,
                    now,
                ),
            )
            created = cur.rowcount > 0
            row = self._conn.execute(
                """SELECT * FROM supervisor_autoresearch_experiments
                   WHERE signal_key=?""",
                (signal_key,),
            ).fetchone()
            self._conn.commit()
            if row is None:
                raise RuntimeError("AutoResearch experiment draft was not persisted")
            return _autoresearch_experiment_row_to_dict(row), created

    def get_autoresearch_experiment(self, *, experiment_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT * FROM supervisor_autoresearch_experiments
               WHERE experiment_id=?""",
            (experiment_id,),
        ).fetchone()
        return _autoresearch_experiment_row_to_dict(row) if row is not None else None

    def list_autoresearch_experiment_queue(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status=?"
            params.append(status)
        params.append(int(limit))
        rows = self._conn.execute(
            f"""SELECT * FROM supervisor_autoresearch_experiments
                {where}
                ORDER BY created_at ASC, experiment_id ASC
                LIMIT ?""",
            tuple(params),
        ).fetchall()
        return [_autoresearch_experiment_row_to_dict(row) for row in rows]

    def activate_autoresearch_experiment(
        self,
        *,
        experiment_id: str,
        operator: str,
        approval_channel: str,
        activated_at: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time()) if activated_at is None else int(activated_at)
        with self._write_lock:
            row = self._conn.execute(
                """SELECT * FROM supervisor_autoresearch_experiments
                   WHERE experiment_id=?""",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
            if row["status"] == "draft":
                self._conn.execute(
                    """UPDATE supervisor_autoresearch_experiments
                          SET status='runnable',
                              activated_at=?,
                              activated_by=?,
                              activation_channel=?,
                              updated_at=?
                        WHERE experiment_id=?""",
                    (now, operator, approval_channel, now, experiment_id),
                )
            updated = self._conn.execute(
                """SELECT * FROM supervisor_autoresearch_experiments
                   WHERE experiment_id=?""",
                (experiment_id,),
            ).fetchone()
            self._conn.commit()
            if updated is None:
                raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
            return _autoresearch_experiment_row_to_dict(updated)

    def mark_autoresearch_experiment_run_started(
        self,
        *,
        experiment_id: str,
        run_id: str,
        started_at: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time()) if started_at is None else int(started_at)
        with self._write_lock:
            self._conn.execute(
                """UPDATE supervisor_autoresearch_experiments
                      SET status='running',
                          last_run_id=?,
                          last_run_started_at=?,
                          updated_at=?
                    WHERE experiment_id=? AND status='runnable'""",
                (run_id, now, now, experiment_id),
            )
            row = self._conn.execute(
                """SELECT * FROM supervisor_autoresearch_experiments
                   WHERE experiment_id=?""",
                (experiment_id,),
            ).fetchone()
            self._conn.commit()
            if row is None:
                raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
            return _autoresearch_experiment_row_to_dict(row)

    def complete_autoresearch_experiment_run(
        self,
        *,
        experiment_id: str,
        status: str,
        report_ref: str = "",
        report_sha256: str = "",
        completed_at: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time()) if completed_at is None else int(completed_at)
        with self._write_lock:
            self._conn.execute(
                """UPDATE supervisor_autoresearch_experiments
                      SET status=?,
                          report_ref=?,
                          report_sha256=?,
                          updated_at=?
                    WHERE experiment_id=?""",
                (status, report_ref, report_sha256, now, experiment_id),
            )
            row = self._conn.execute(
                """SELECT * FROM supervisor_autoresearch_experiments
                   WHERE experiment_id=?""",
                (experiment_id,),
            ).fetchone()
            self._conn.commit()
            if row is None:
                raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
            return _autoresearch_experiment_row_to_dict(row)

    def count_autoresearch_experiments_started_since(self, *, started_since: int) -> int:
        row = self._conn.execute(
            """SELECT COUNT(*) AS count
                 FROM supervisor_autoresearch_experiments
                WHERE last_run_started_at IS NOT NULL
                  AND last_run_started_at >= ?""",
            (int(started_since),),
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    def list_autoresearch_signal_events(self, *, limit: int = 10000) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT event_id, run_id, ts, source, kind, payload_json
                 FROM events
                WHERE kind IN (
                    'dual_agent_gate_result',
                    'dual_agent_planning_validation',
                    'dual_agent_dynamic_workflow_receipt_validation',
                    'dual_agent_runtime_evidence',
                    'independent_reviewer_review',
                    'tri_agent_cursor_review',
                    'dual_agent_probe_cohort',
                    'supervisor_probe_cohort',
                    'probe_cohort_summary'
                  )
                   OR source='drift'
                   OR kind LIKE '%probe_cohort%'
                ORDER BY event_id ASC
                LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "run_id": row["run_id"],
                "ts": int(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def list_policy_proposal_approval_events(
        self,
        *,
        proposal_id: str | None = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT event_id, run_id, ts, source, kind, payload_json
                 FROM events
                WHERE kind='autoresearch_policy_proposal_approved'
                ORDER BY event_id ASC
                LIMIT ?""",
            (int(limit),),
        ).fetchall()
        events: list[dict[str, Any]] = []
        expected = str(proposal_id or "").strip()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if expected and str(payload.get("proposal_id") or "") != expected:
                continue
            events.append({
                "event_id": int(row["event_id"]),
                "run_id": row["run_id"],
                "ts": int(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                "payload": payload,
            })
        return events

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
        recovery_point: str | None = None,
        request_payload_json: str | None = None,
        config_path: str | None = None,
        pid: int | None = None,
        returncode: int | None = None,
        error: str | None = None,
    ) -> None:
        now = int(time.time())
        recovery_point_value = recovery_point or _workflow_job_recovery_point(
            status=status,
            pid=pid,
        )
        with self._write_lock:
            self._conn.execute(
                """INSERT INTO dual_agent_workflow_jobs(
                       job_id, run_id, task_id, cwd, status, pid,
                       request_path, result_path, log_path, idempotency_token,
                       recovery_point, request_payload_json, config_path,
                       returncode, error, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       status=excluded.status,
                       pid=excluded.pid,
                       idempotency_token=COALESCE(
                           excluded.idempotency_token,
                           dual_agent_workflow_jobs.idempotency_token
                       ),
                       recovery_point=excluded.recovery_point,
                       recovery_claim_token=NULL,
                       recovery_claimed_at=NULL,
                       request_payload_json=COALESCE(
                           excluded.request_payload_json,
                           dual_agent_workflow_jobs.request_payload_json
                       ),
                       config_path=COALESCE(
                           excluded.config_path,
                           dual_agent_workflow_jobs.config_path
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
                    recovery_point_value,
                    request_payload_json,
                    config_path,
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
        request_payload_json: str | None = None,
        config_path: str | None = None,
    ) -> tuple[sqlite3.Row, bool]:
        """Atomically reserve a detached workflow job before launching its worker.

        The idempotency token is the deduplication boundary for submit retries.
        Spawning is intentionally outside this reservation boundary.
        """
        now = int(time.time())
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    """SELECT * FROM dual_agent_workflow_jobs
                       WHERE idempotency_token=?
                       ORDER BY CASE WHEN recovery_point != 'terminal' THEN 0 ELSE 1 END,
                                created_at ASC
                       LIMIT 1""",
                    (idempotency_token,),
                ).fetchone()
                if existing is not None:
                    self._conn.commit()
                    return existing, False

                try:
                    self._conn.execute(
                        """INSERT INTO dual_agent_workflow_jobs(
                               job_id, run_id, task_id, cwd, status, pid,
                               request_path, result_path, log_path,
                               idempotency_token, recovery_point, request_payload_json,
                               config_path, returncode, error, created_at, updated_at)
                           VALUES(?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 'reserved', ?, ?, NULL, NULL, ?, ?)""",
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
                    )
                except sqlite3.IntegrityError:
                    existing = self._conn.execute(
                        """SELECT * FROM dual_agent_workflow_jobs
                           WHERE idempotency_token=?
                           ORDER BY CASE WHEN recovery_point != 'terminal' THEN 0 ELSE 1 END,
                                    created_at ASC
                           LIMIT 1""",
                        (idempotency_token,),
                    ).fetchone()
                    if existing is None:
                        raise
                    self._conn.commit()
                    return existing, False
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
        recovery_point: str | None = None,
        request_payload_json: str | None = None,
        config_path: str | None = None,
        leased_by: str | None = None,
        lease_expires_at: int | None = None,
        heartbeat_at: int | None = None,
        dispatch_attempts: int | None = None,
        next_dispatch_at: int | None = None,
        parked_reason: str | None = None,
        clear_lease: bool = False,
        clear_next_dispatch_at: bool = False,
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
        if recovery_point is not None:
            assignments.append("recovery_point=?")
            params.append(recovery_point)
            assignments.append("recovery_claim_token=NULL")
            assignments.append("recovery_claimed_at=NULL")
        if request_payload_json is not None:
            assignments.append("request_payload_json=?")
            params.append(request_payload_json)
        if config_path is not None:
            assignments.append("config_path=?")
            params.append(config_path)
        if leased_by is not None:
            assignments.append("leased_by=?")
            params.append(leased_by)
        if lease_expires_at is not None:
            assignments.append("lease_expires_at=?")
            params.append(lease_expires_at)
        if heartbeat_at is not None:
            assignments.append("heartbeat_at=?")
            params.append(heartbeat_at)
        if dispatch_attempts is not None:
            assignments.append("dispatch_attempts=?")
            params.append(dispatch_attempts)
        if next_dispatch_at is not None:
            assignments.append("next_dispatch_at=?")
            params.append(next_dispatch_at)
        if parked_reason is not None:
            assignments.append("parked_reason=?")
            params.append(parked_reason)
        if clear_lease:
            assignments.append("leased_by=NULL")
            assignments.append("lease_expires_at=NULL")
            assignments.append("heartbeat_at=NULL")
        if clear_next_dispatch_at:
            assignments.append("next_dispatch_at=NULL")
        params.append(job_id)
        with self._write_lock:
            self._conn.execute(
                f"""UPDATE dual_agent_workflow_jobs
                       SET {", ".join(assignments)}
                     WHERE job_id=?""",
                params,
            )
            self._conn.commit()

    def count_active_dual_agent_workflow_job_leases(self, *, now: int) -> int:
        row = self._conn.execute(
            """SELECT COUNT(*) AS count
               FROM dual_agent_workflow_jobs
               WHERE recovery_point='spawned'
                 AND status='running'
                 AND terminal_outcome_json IS NULL
                 AND leased_by IS NOT NULL
                 AND lease_expires_at IS NOT NULL
                 AND lease_expires_at > ?""",
            (now,),
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    def claim_next_dual_agent_workflow_job_for_dispatch(
        self,
        *,
        dispatcher_id: str,
        lease_ttl_s: int,
        now: int,
        job_id: str | None = None,
    ) -> sqlite3.Row | None:
        lease_expires_at = now + max(1, int(lease_ttl_s))
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                filters = [
                    "recovery_point IN ('reserved', 'request_written')",
                    """status NOT IN ('parked', 'accepted', 'blocked',
                                      'cancelled', 'completed', 'denied', 'failed')""",
                    "terminal_outcome_json IS NULL",
                    "pid IS NULL",
                    "(next_dispatch_at IS NULL OR next_dispatch_at <= ?)",
                    """(
                           leased_by IS NULL
                        OR lease_expires_at IS NULL
                        OR lease_expires_at <= ?
                       )""",
                ]
                params: list[Any] = [now, now]
                if job_id is not None:
                    filters.append("job_id=?")
                    params.append(job_id)
                row = self._conn.execute(
                    f"""SELECT *
                       FROM dual_agent_workflow_jobs
                       WHERE {" AND ".join(filters)}
                       ORDER BY created_at ASC, job_id ASC
                       LIMIT 1""",
                    params,
                ).fetchone()
                if row is None:
                    self._conn.commit()
                    return None
                self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET leased_by=?,
                              lease_expires_at=?,
                              heartbeat_at=?,
                              updated_at=?
                        WHERE job_id=?""",
                    (dispatcher_id, lease_expires_at, now, now, row["job_id"]),
                )
                claimed = self._conn.execute(
                    "SELECT * FROM dual_agent_workflow_jobs WHERE job_id=?",
                    (row["job_id"],),
                ).fetchone()
                self._conn.commit()
                return claimed
            except Exception:
                self._conn.rollback()
                raise

    def clear_dual_agent_workflow_job_lease(
        self,
        *,
        job_id: str,
        next_dispatch_at: int | None = None,
        dispatch_attempts: int | None = None,
        error: str | None = None,
    ) -> sqlite3.Row | None:
        assignments = [
            "leased_by=NULL",
            "lease_expires_at=NULL",
            "heartbeat_at=NULL",
            "updated_at=?",
        ]
        params: list[Any] = [int(time.time())]
        if next_dispatch_at is not None:
            assignments.append("next_dispatch_at=?")
            params.append(next_dispatch_at)
        if dispatch_attempts is not None:
            assignments.append("dispatch_attempts=?")
            params.append(dispatch_attempts)
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
            row = self._conn.execute(
                "SELECT * FROM dual_agent_workflow_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            self._conn.commit()
            return row

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
            cursor = self._conn.execute(
                """UPDATE dual_agent_workflow_jobs
                      SET lease_expires_at=?,
                          heartbeat_at=?,
                          updated_at=?
                    WHERE job_id=?
                      AND leased_by=?
                      AND recovery_point='spawned'
                      AND terminal_outcome_json IS NULL""",
                (lease_expires_at, now_value, now_value, job_id, leased_by),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def park_dual_agent_workflow_job(
        self,
        *,
        job_id: str,
        reason: str,
    ) -> sqlite3.Row | None:
        now = int(time.time())
        with self._write_lock:
            self._conn.execute(
                """UPDATE dual_agent_workflow_jobs
                      SET status='parked',
                          error=?,
                          parked_reason=?,
                          leased_by=NULL,
                          lease_expires_at=NULL,
                          heartbeat_at=NULL,
                          recovery_claim_token=NULL,
                          recovery_claimed_at=NULL,
                          updated_at=?
                    WHERE job_id=?""",
                (reason, reason, now, job_id),
            )
            row = self._conn.execute(
                "SELECT * FROM dual_agent_workflow_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            self._conn.commit()
            return row

    def list_dual_agent_workflow_job_leases(self) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """SELECT *
               FROM dual_agent_workflow_jobs
               WHERE leased_by IS NOT NULL
                 AND terminal_outcome_json IS NULL
                 AND status!='parked'
               ORDER BY updated_at ASC, job_id ASC"""
        ))

    def claim_dual_agent_workflow_job_recovery_point(
        self,
        *,
        job_id: str,
        expected_recovery_point: str,
        claim_token: str,
        claim_ttl_s: int = 60,
    ) -> sqlite3.Row | None:
        """Claim ownership to drive one recovery phase.

        This is a compare-and-set boundary for poll-side recovery. A caller that
        only holds a stale job row must win this claim before writing a request
        file or spawning a worker.
        """
        now = int(time.time())
        stale_before = now - max(0, claim_ttl_s)
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET recovery_claim_token=?,
                              recovery_claimed_at=?,
                              updated_at=?
                        WHERE job_id=?
                          AND recovery_point=?
                          AND pid IS NULL
                          AND terminal_outcome_json IS NULL
                          AND (
                                recovery_claim_token IS NULL
                             OR recovery_claimed_at IS NULL
                             OR recovery_claimed_at <= ?
                          )""",
                    (
                        claim_token,
                        now,
                        now,
                        job_id,
                        expected_recovery_point,
                        stale_before,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    """SELECT * FROM dual_agent_workflow_jobs
                       WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                self._conn.commit()
                return row
            except Exception:
                self._conn.rollback()
                raise

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
                              recovery_point='terminal',
                              recovery_claim_token=NULL,
                              recovery_claimed_at=NULL,
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              heartbeat_at=NULL,
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

    def list_dual_agent_workflow_jobs(
        self,
        *,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(str(status))
        if active_only:
            clauses.append(
                """status NOT IN ('parked', 'accepted', 'blocked',
                                  'cancelled', 'completed', 'denied', 'failed')"""
            )
            clauses.append("terminal_outcome_json IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT *
                FROM dual_agent_workflow_jobs
                {where}
                ORDER BY updated_at DESC, created_at DESC, job_id ASC
                LIMIT ?""",
            (*params, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_active_dual_agent_workflow_steps(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT *
               FROM dual_agent_workflow_steps
               WHERE status NOT IN ('accepted', 'blocked', 'denied', 'failed', 'cancelled')
               ORDER BY updated_at DESC, created_at DESC, id ASC
               LIMIT ?""",
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(row) for row in rows]

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
