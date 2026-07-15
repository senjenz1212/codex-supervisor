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
import base64
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .evidence_ledger import (
    EVIDENCE_COMMIT_EVENT_KIND,
    EVIDENCE_COMMIT_EVENT_SOURCE,
    NATIVE_GENESIS,
    LedgerVerification,
    build_ledger_fields,
    canonical_json_bytes,
    canonical_payload_hash,
    prepare_event_payload,
    sha256_hex,
    verify_event_chain,
    verify_event_chain_structure,
)
from .target.types import ScopeContract
from .redaction import redact
from .quality_projection import (
    QUALITY_TREND_PROJECTION_EVENT,
    assert_generic_event_kind_allowed,
    canonical_quality_trend_projection_row,
    quality_trend_projection_event_payload,
    rebuild_quality_trend_projection,
)
from .schema_migrations import run_forward_migrations
from .lessons import canonical_lesson_id
from .lessons import canonical_lesson_key

if TYPE_CHECKING:
    from .ledger_checkpoints import LedgerCheckpointCoordinator


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
HISTORICAL_OPERATION_EVENT_SOURCE = "historical_evaluation"
HISTORICAL_OPERATION_EVENT_PREFIX = "historical_operation."
HISTORICAL_OPERATION_OWNER_FENCED_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "historical_operation.requested",
        "historical_operation.completed",
        "historical_operation.failed",
    }
)


def _historical_lease_duration_seconds(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(
            "historical operation lease duration must be positive"
        )
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "historical operation lease duration must be positive"
        ) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(
            "historical operation lease duration must be positive"
        )
    return duration


def _historical_lease_is_expired(
    lease_value: Any,
    *,
    state_now: float,
    lease_duration_s: float,
) -> bool:
    if isinstance(lease_value, bool):
        return False
    try:
        lease_at = float(lease_value)
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(lease_at)
        and lease_at <= state_now - lease_duration_s
    )

TERMINAL_WORKFLOW_JOB_PROTECTED_FIELDS: tuple[str, ...] = (
    "job_id",
    "run_id",
    "task_id",
    "result_path",
    "status",
    "pid",
    "worker_pgid",
    "worker_started_at",
    "worker_prepared_at",
    "worker_containment_id",
    "worker_reaped_at",
    "cleanup_attempts",
    "cleanup_escalated_at",
    "recovery_point",
    "terminal_status",
    "terminal_outcome_json",
    "terminal_outcome_recorded_at",
    "returncode",
    "error",
)


def is_postgres_state_dsn(value: str | Path) -> bool:
    raw = str(value).strip().lower()
    return raw.startswith(("postgres://", "postgresql://"))


def assert_historical_operation_event_source(
    *,
    source: str,
    kind: str,
) -> None:
    if (
        str(kind).startswith(HISTORICAL_OPERATION_EVENT_PREFIX)
        and str(source) != HISTORICAL_OPERATION_EVENT_SOURCE
    ):
        raise ValueError(
            "historical operation events require the dedicated source"
        )


def assert_public_event_kind_allowed(kind: str) -> None:
    """Keep capability-owned historical events off the generic write surface."""
    if str(kind) == EVIDENCE_COMMIT_EVENT_KIND:
        raise ValueError(
            "reserved evidence-commit event requires the dedicated writer"
        )
    if str(kind).startswith(HISTORICAL_OPERATION_EVENT_PREFIX):
        raise ValueError(
            "historical operation events require the dedicated writer"
        )


def _assert_evidence_committer_owner(owner: Any) -> None:
    """Limit capability binding to the exact trusted committer implementation."""
    from .evidence_committer import EvidenceCommitter

    if type(owner) is not EvidenceCommitter:
        raise PermissionError(
            "evidence-commit write capability requires EvidenceCommitter"
        )


def canonical_terminal_outcome_json(outcome: dict[str, Any]) -> str:
    """Canonical redacted workflow-result JSON for ledger storage/comparison."""
    return json.dumps(redact(outcome), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_terminal_completion_semantics(
    *,
    job_id: Any,
    run_id: Any,
    task_id: Any,
    result_path: Any,
    status: Any,
    terminal_status: Any,
    terminal_outcome_json: str,
    returncode: Any,
    error: Any,
) -> dict[str, Any]:
    """Canonicalize identity and semantic fields for terminal CAS comparison."""
    return {
        "job_id": str(job_id),
        "run_id": str(run_id),
        "task_id": str(task_id),
        "result_path": str(result_path),
        "status": str(status),
        "terminal_status": str(terminal_status),
        "terminal_outcome": json.loads(str(terminal_outcome_json)),
        "returncode": None if returncode is None else int(returncode),
        "error": None if error is None else str(error),
    }


def canonical_terminal_completion_record(
    *,
    job_id: Any,
    run_id: Any,
    task_id: Any,
    result_path: Any,
    status: Any,
    recovery_point: Any,
    terminal_status: Any,
    terminal_outcome_json: str,
    terminal_outcome_recorded_at: Any,
    returncode: Any,
    error: Any,
) -> dict[str, Any]:
    """Canonicalize the complete persisted terminal record for ledger binding."""
    return {
        **canonical_terminal_completion_semantics(
            job_id=job_id,
            run_id=run_id,
            task_id=task_id,
            result_path=result_path,
            status=status,
            terminal_status=terminal_status,
            terminal_outcome_json=terminal_outcome_json,
            returncode=returncode,
            error=error,
        ),
        "recovery_point": str(recovery_point),
        "terminal_outcome_recorded_at": int(terminal_outcome_recorded_at),
    }


def terminal_completion_record_sha256(record: dict[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(record))


def terminal_completion_conflict_sha256(
    *,
    original: Mapping[str, Any],
    conflicting: Mapping[str, Any],
    validation_error: str | None,
) -> str:
    return sha256_hex(
        canonical_json_bytes(
            {
                "original": dict(original),
                "conflicting": dict(conflicting),
                "validation_error": validation_error,
            }
        )
    )


def validate_terminal_completion(
    *,
    job_id: str,
    run_id: Any,
    task_id: Any,
    status: Any,
    terminal_status: Any,
    terminal_outcome: dict[str, Any],
) -> str:
    """Validate one terminal completion against the reserved job identity."""
    normalized_status = str(status).strip()
    if normalized_status not in TERMINAL_WORKFLOW_JOB_STATUSES:
        raise ValueError(
            f"terminal status must be one of "
            f"{sorted(TERMINAL_WORKFLOW_JOB_STATUSES)}"
        )
    outcome_status = terminal_outcome.get("status")
    normalized_terminal_status = str(
        terminal_status
        if terminal_status is not None
        else outcome_status if outcome_status is not None else normalized_status
    ).strip()
    if normalized_terminal_status != normalized_status:
        raise ValueError(
            "terminal status mismatch: "
            f"status={normalized_status!r} "
            f"terminal_status={normalized_terminal_status!r}"
        )
    if "status" in terminal_outcome and str(outcome_status).strip() != normalized_status:
        raise ValueError(
            "terminal outcome status mismatch: "
            f"status={normalized_status!r} outcome_status={outcome_status!r}"
        )
    expected_identity = {
        "job_id": str(job_id),
        "run_id": str(run_id),
        "task_id": str(task_id),
    }
    for identity_field, expected in expected_identity.items():
        if identity_field not in terminal_outcome:
            continue
        observed = str(terminal_outcome[identity_field])
        if observed != expected:
            raise ValueError(
                f"terminal outcome {identity_field} mismatch: "
                f"expected={expected!r} observed={observed!r}"
            )
    return normalized_terminal_status


def assert_terminal_workflow_job_mutation_allowed(
    row: Any,
    *,
    attempted: dict[str, Any],
) -> None:
    """Reject an API write that would alter a persisted terminal record."""
    if row is None or row["terminal_outcome_json"] is None:
        return
    conflicts = [
        field
        for field, value in attempted.items()
        if field in TERMINAL_WORKFLOW_JOB_PROTECTED_FIELDS
        and value != row[field]
    ]
    if conflicts:
        raise RuntimeError(
            "terminal workflow job is immutable: "
            + ", ".join(sorted(conflicts))
        )


def validate_quality_audit_counts(
    *,
    sample_size: Any,
    false_accept_count: Any,
    false_accept_denominator: Any,
) -> tuple[int, int, int, float]:
    """Return validated audit counts and their derived false-accept rate."""
    if any(
        isinstance(value, bool)
        for value in (
            sample_size,
            false_accept_count,
            false_accept_denominator,
        )
    ):
        raise ValueError("invalid quality audit counts: booleans are not counts")
    try:
        sample = int(sample_size)
        false_count = int(false_accept_count)
        denominator = int(false_accept_denominator)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "invalid quality audit counts: counts must be integers"
        ) from exc
    if (
        sample < 0
        or false_count < 0
        or denominator < 0
        or false_count > denominator
        or denominator > sample
    ):
        raise ValueError(
            "invalid quality audit counts: require "
            "0 <= false_accept_count <= false_accept_denominator <= sample_size"
        )
    rate = (false_count / denominator) if denominator else 0.0
    return sample, false_count, denominator, rate


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
  event_id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id                  TEXT NOT NULL,
  event_sequence          INTEGER NOT NULL CHECK(event_sequence > 0),
  ts                      INTEGER NOT NULL,
  source                  TEXT NOT NULL,
  kind                    TEXT NOT NULL,
  payload_json            TEXT NOT NULL,
  previous_event_hash     TEXT,
  event_hash              TEXT NOT NULL,
  canonical_payload_hash  TEXT NOT NULL,
  artifact_manifest_hash  TEXT NOT NULL,
  ledger_genesis_kind     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_run_event ON events(run_id, event_id);

CREATE TABLE IF NOT EXISTS event_idempotency_claims (
  run_id          TEXT NOT NULL,
  kind            TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  event_id        INTEGER NOT NULL,
  source          TEXT NOT NULL,
  payload_sha256  TEXT NOT NULL,
  created_at      INTEGER NOT NULL,
  PRIMARY KEY(run_id, kind, idempotency_key),
  UNIQUE(event_id),
  CHECK(event_id > 0)
);
CREATE TRIGGER IF NOT EXISTS event_idempotency_claims_no_update
BEFORE UPDATE ON event_idempotency_claims
BEGIN
  SELECT RAISE(ABORT, 'event idempotency claims are immutable');
END;
CREATE TRIGGER IF NOT EXISTS event_idempotency_claims_no_delete
BEFORE DELETE ON event_idempotency_claims
BEGIN
  SELECT RAISE(ABORT, 'event idempotency claims are immutable');
END;

CREATE TABLE IF NOT EXISTS verdicts (
  verdict_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id   TEXT,
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

CREATE TABLE IF NOT EXISTS source_line_ingestions (
  source_line_id TEXT PRIMARY KEY,
  path           TEXT NOT NULL,
  start_offset   INTEGER NOT NULL,
  end_offset     INTEGER NOT NULL,
  raw_sha256     TEXT NOT NULL,
  run_id         TEXT NOT NULL,
  source         TEXT NOT NULL,
  status         TEXT NOT NULL CHECK(status IN ('ingested', 'ignored', 'dead_letter')),
  event_ids_json TEXT NOT NULL,
  raw_line_b64   TEXT,
  error_json     TEXT,
  created_at     INTEGER NOT NULL,
  UNIQUE(path, start_offset, end_offset)
);
CREATE INDEX IF NOT EXISTS idx_source_line_ingestions_run
  ON source_line_ingestions(run_id, created_at);

CREATE TABLE IF NOT EXISTS decision_outbox (
  decision_id      TEXT PRIMARY KEY,
  idempotency_key  TEXT NOT NULL UNIQUE,
  kind             TEXT NOT NULL,
  run_id           TEXT NOT NULL,
  payload_json     TEXT NOT NULL,
  status           TEXT NOT NULL CHECK(
    status IN ('pending', 'leased', 'acked', 'dead_letter')
  ),
  attempts         INTEGER NOT NULL DEFAULT 0,
  available_at     REAL NOT NULL,
  lease_token      TEXT,
  leased_by        TEXT,
  lease_expires_at REAL,
  last_error       TEXT,
  created_at       REAL NOT NULL,
  updated_at       REAL NOT NULL,
  acked_at         REAL,
  dead_lettered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_decision_outbox_dispatch
  ON decision_outbox(status, available_at, lease_expires_at, created_at);

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

CREATE TABLE IF NOT EXISTS historical_operation_claims (
  operation_id  TEXT PRIMARY KEY,
  request_hash  TEXT NOT NULL,
  operation     TEXT NOT NULL CHECK(operation IN ('rerun', 'regrade', 'replay')),
  status        TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
  terminal_event_id INTEGER,
  execution_owner_token TEXT,
  execution_generation INTEGER NOT NULL DEFAULT 0,
  execution_heartbeat_at REAL,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_historical_operation_claims_status
  ON historical_operation_claims(status, updated_at);

CREATE TABLE IF NOT EXISTS dual_agent_workflow_jobs (
  job_id       TEXT PRIMARY KEY,
  run_id       TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  cwd          TEXT NOT NULL,
  status       TEXT NOT NULL,
  pid          INTEGER,
  worker_pgid  INTEGER,
  worker_started_at REAL,
  worker_prepared_at REAL,
  worker_containment_id TEXT,
  worker_reaped_at INTEGER,
  cleanup_attempts INTEGER NOT NULL DEFAULT 0,
  cleanup_escalated_at INTEGER,
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

CREATE TABLE IF NOT EXISTS quality_trend_audits (
  run_id                         TEXT NOT NULL,
  gate                           TEXT NOT NULL,
  sample_size                    INTEGER NOT NULL,
  false_accept_count             INTEGER NOT NULL,
  false_accept_denominator       INTEGER NOT NULL,
  false_accept_rate              REAL NOT NULL,
  audit_details_json             TEXT NOT NULL DEFAULT '{}',
  computed_at                    INTEGER NOT NULL,
  PRIMARY KEY(run_id, gate, computed_at),
  CHECK (
       sample_size >= 0
   AND false_accept_count >= 0
   AND false_accept_denominator >= 0
   AND false_accept_count <= false_accept_denominator
   AND false_accept_denominator <= sample_size
   AND false_accept_rate >= 0.0
   AND false_accept_rate <= 1.0
  )
);
CREATE INDEX IF NOT EXISTS idx_quality_trend_audits_run_gate
  ON quality_trend_audits(run_id, gate, computed_at);

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
    decision_id: str = ""
    lease_token: str = ""
    attempt_count: int = 0


@dataclass(frozen=True)
class SourceLineIngestion:
    source_line_id: str
    event_ids: tuple[int, ...]
    inserted: bool
    status: str


class _DecisionOutboxView:
    """Compatibility view for callers that inspected the old asyncio queue."""

    def __init__(self, state: State) -> None:
        self._state = state

    def get_nowait(self) -> Decision:
        decision = self._state.claim_decision(
            worker_id="legacy-queue-view",
            lease_s=300,
        )
        if decision is None:
            raise asyncio.QueueEmpty
        return decision

    def qsize(self) -> int:
        return self._state.available_decision_count()

    def empty(self) -> bool:
        return self.qsize() == 0


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


def _quality_trend_audit_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key in (
        "sample_size",
        "false_accept_count",
        "false_accept_denominator",
        "computed_at",
    ):
        payload[key] = int(payload.get(key) or 0)
    payload["false_accept_rate"] = float(payload.get("false_accept_rate") or 0.0)
    try:
        payload["audit_details"] = json.loads(
            str(payload.pop("audit_details_json") or "{}")
        )
    except json.JSONDecodeError:
        payload["audit_details"] = {}
    return payload


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

    def __new__(
        cls,
        db_path: str,
        *,
        ledger_checkpoint_coordinator: LedgerCheckpointCoordinator | None = None,
    ):
        if cls is State and is_postgres_state_dsn(db_path):
            from .postgres_state import PostgresState

            return PostgresState(
                db_path,
                ledger_checkpoint_coordinator=ledger_checkpoint_coordinator,
            )
        return super().__new__(cls)

    def __init__(
        self,
        db_path: str,
        *,
        ledger_checkpoint_coordinator: LedgerCheckpointCoordinator | None = None,
    ):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._ledger_checkpoint_coordinator = ledger_checkpoint_coordinator
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA recursive_triggers=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        run_forward_migrations(self._conn)
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_events_run_sequence
               ON events(run_id, event_sequence)"""
        )
        self._conn.commit()
        self._lock = asyncio.Lock()
        self._write_lock = threading.RLock()
        self.__evidence_commit_write_capability = object()
        self._decision_wakeup = asyncio.Event()
        self._decision_worker_id = (
            f"state-{os.getpid()}-{uuid.uuid4().hex}"
        )
        self.decisions = _DecisionOutboxView(self)
        self.reconcile_event_checkpoints()

    @property
    def event_ledger_assurance(self) -> str:
        """Mark whether writes are externally checkpointed or diagnostic-only."""
        if self._ledger_checkpoint_coordinator is None:
            return "diagnostic-only"
        return self._ledger_checkpoint_coordinator.assurance

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
        merged = ScopeContract(
            allowed_paths=scope.allowed_paths,
            related_paths=scope.related_paths,
            protected_paths=scope.protected_paths,
            never_touch_patterns=_merge_never_touch(scope.never_touch_patterns),
        )
        now = int(time.time())
        safe_config = redact(config_snapshot or {})
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT 1 FROM run_snapshots WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if existing is not None:
                    # Snapshot already exists. Don't touch it.
                    self._conn.commit()
                    return
                self._conn.execute(
                    """INSERT OR IGNORE INTO runs(
                        run_id, session_id, rollout_path, task, scope_hints,
                        started_at, status)
                       VALUES(?, ?, ?, ?, ?, ?, 'running')""",
                    (
                        run_id,
                        session_id,
                        rollout_path,
                        task,
                        json.dumps(list(merged.allowed_paths)),
                        now,
                    ),
                )
                self._conn.execute(
                    """INSERT INTO run_snapshots(
                        run_id, config_json, scope_contract_json,
                        target_kind, codex_cli_version, created_at)
                       VALUES(?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        json.dumps(safe_config),
                        json.dumps(merged.to_dict()),
                        target_kind,
                        codex_cli_version,
                        now,
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

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

    def bind_run_session(
        self,
        *,
        run_id: str,
        session_id: str,
        rollout_path: str,
    ) -> sqlite3.Row:
        """Atomically bind a previously pending workflow to its real session."""
        target_session = str(session_id).strip()
        if not target_session:
            raise ValueError("session_id is required")
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"run not found: {run_id}")
                conflict = self._conn.execute(
                    """SELECT run_id FROM runs
                       WHERE session_id=? AND run_id!=?
                       LIMIT 1""",
                    (target_session, run_id),
                ).fetchone()
                if conflict is not None:
                    raise RuntimeError(
                        "target session is already bound to another run: "
                        f"{target_session}"
                    )
                self._conn.execute(
                    """UPDATE runs
                          SET session_id=?, rollout_path=?
                        WHERE run_id=?""",
                    (target_session, str(rollout_path), run_id),
                )
                rebound = self._conn.execute(
                    "SELECT * FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                self._conn.commit()
                if rebound is None:
                    raise RuntimeError("bound run disappeared")
                return rebound
            except Exception:
                self._conn.rollback()
                raise

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()

    # --- events ---
    def _event_payload(self, *, run_id: str, source: str, kind: str, payload: dict) -> dict:
        return prepare_event_payload(
            run_id=run_id,
            source=source,
            kind=kind,
            payload=payload,
        )

    def _insert_event_unlocked(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict,
        ts: int | None = None,
    ) -> int:
        event_ts = int(time.time()) if ts is None else int(ts)
        previous = self._conn.execute(
            """SELECT event_hash, event_sequence
                 FROM events
                WHERE run_id=?
                ORDER BY event_sequence DESC
                LIMIT 1""",
            (run_id,),
        ).fetchone()
        previous_event_hash = (
            str(previous["event_hash"])
            if previous is not None and previous["event_hash"] is not None
            else None
        )
        event_sequence = (
            int(previous["event_sequence"]) + 1
            if previous is not None
            else 1
        )
        fields = build_ledger_fields(
            run_id=run_id,
            event_sequence=event_sequence,
            ts=event_ts,
            source=source,
            kind=kind,
            payload=payload,
            previous_event_hash=previous_event_hash,
            ledger_genesis_kind=(
                NATIVE_GENESIS if previous_event_hash is None else None
            ),
        )
        cur = self._conn.execute(
            """INSERT INTO events(
                 run_id, event_sequence, ts, source, kind, payload_json,
                 previous_event_hash, event_hash, canonical_payload_hash,
                 artifact_manifest_hash, ledger_genesis_kind)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                event_sequence,
                event_ts,
                source,
                kind,
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
                fields.previous_event_hash,
                fields.event_hash,
                fields.canonical_payload_hash,
                fields.artifact_manifest_hash,
                fields.ledger_genesis_kind,
            ),
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

    def _event_ledger_rows(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """SELECT event_id, run_id, event_sequence, ts, source, kind,
                          payload_json,
                          previous_event_hash, event_hash,
                          canonical_payload_hash, artifact_manifest_hash,
                          ledger_genesis_kind
                     FROM events
                    WHERE run_id=?
                    ORDER BY event_id ASC""",
                (run_id,),
            ).fetchall()
        )

    def _coordinate_committed_event(
        self,
        *,
        run_id: str,
        event_id: int,
        event_kind: str,
    ) -> None:
        coordinator = self._ledger_checkpoint_coordinator
        if coordinator is None or event_id <= 0:
            return
        event = self._conn.execute(
            """SELECT event_id, event_sequence
                 FROM events
                WHERE run_id=? AND event_id=?""",
            (run_id, event_id),
        ).fetchone()
        if event is None:
            raise RuntimeError(
                "committed event disappeared before checkpoint coordination"
            )
        coordinator.coordinate_event(
            run_id=run_id,
            event_id=event["event_id"],
            event_count=int(event["event_sequence"]),
            event_kind=event_kind,
            events_loader=lambda: self._event_ledger_rows(run_id),
        )

    def ensure_event_checkpoint(
        self,
        *,
        run_id: str,
        event_id: int,
        event_kind: str,
    ) -> None:
        """Retry idempotent checkpoint publication for an existing event."""
        with self._write_lock:
            self._coordinate_committed_event(
                run_id=run_id,
                event_id=int(event_id),
                event_kind=event_kind,
            )

    def _trusted_checkpoint_event_count(self, run_id: str) -> int:
        pin_store = getattr(
            self._ledger_checkpoint_coordinator,
            "trusted_pin_store",
            None,
        )
        if pin_store is None:
            return 0
        try:
            latest = pin_store.latest(run_id)
            count = 0 if latest is None else int(latest["event_count"])
        except Exception:
            return 0
        return max(count, 0)

    def reconcile_event_checkpoints(
        self,
        *,
        run_id: str | None = None,
    ) -> int:
        """Recover checkpoint publication from the durable event ledger.

        Event insertion commits before external publication. Replaying the
        immutable stream beyond each run's trusted checkpoint head is
        therefore the durable recovery queue; checkpoint and trusted-pin
        writes are idempotent.
        """
        coordinator = self._ledger_checkpoint_coordinator
        if coordinator is None:
            return 0
        with self._write_lock:
            if run_id is None:
                run_ids = [
                    str(row["run_id"])
                    for row in self._conn.execute(
                        """SELECT DISTINCT run_id
                             FROM events
                            ORDER BY run_id ASC"""
                    ).fetchall()
                ]
            else:
                run_ids = [str(run_id)]
            replayed = 0
            for pending_run_id in run_ids:
                trusted_count = self._trusted_checkpoint_event_count(
                    pending_run_id
                )
                rows = self._conn.execute(
                    """SELECT event_id, event_sequence, kind
                         FROM events
                        WHERE run_id=? AND event_sequence>?
                        ORDER BY event_sequence ASC""",
                    (pending_run_id, trusted_count),
                ).fetchall()
                for row in rows:
                    coordinator.coordinate_event(
                        run_id=pending_run_id,
                        event_id=int(row["event_id"]),
                        event_count=int(row["event_sequence"]),
                        event_kind=str(row["kind"]),
                        events_loader=lambda rid=pending_run_id: (
                            self._event_ledger_rows(rid)
                        ),
                    )
                replayed += len(rows)
            return replayed

    def write_event(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict,
        ts: int | None = None,
    ) -> int:
        """Append one event and enforce the configured checkpoint policy.

        In authoritative mode the event commit is durable before external
        checkpoint publication. A publication failure is raised to the caller;
        diagnostic structure verification remains available, while
        authoritative verification stays fail closed against the stale pin.
        """
        assert_generic_event_kind_allowed(kind)
        assert_public_event_kind_allowed(kind)
        return self._write_event_internal(
            run_id=run_id,
            source=source,
            kind=kind,
            payload=payload,
            ts=ts,
        )

    def write_evidence_commit_event(
        self,
        *,
        run_id: str,
        payload: dict[str, Any],
        capability: object,
        ts: int | None = None,
    ) -> int:
        """Append the capability-owned evidence-commit authority event."""
        if capability is not self.__evidence_commit_write_capability:
            raise PermissionError("invalid evidence-commit write capability")
        commit_id = str(payload.get("commit_id") or "").strip()
        if not commit_id:
            raise ValueError("evidence-commit payload requires commit_id")
        return self.write_event_once(
            run_id=run_id,
            source=EVIDENCE_COMMIT_EVENT_SOURCE,
            kind=EVIDENCE_COMMIT_EVENT_KIND,
            payload=payload,
            idempotency_key=(
                "evidence-commit:"
                + canonical_payload_hash({"commit_id": commit_id})
            ),
            ts=ts,
            _reserved_capability=capability,
        )

    def _bind_evidence_commit_writer(self, owner: Any) -> object:
        """Return this state instance's unforgeable writer capability."""
        _assert_evidence_committer_owner(owner)
        return self.__evidence_commit_write_capability

    def assert_evidence_commit_event_authority(
        self,
        *,
        run_id: str,
        commit_id: str,
        event_id: int,
    ) -> None:
        """Require a manifest event to have the committer-owned exact claim."""
        normalized_commit_id = str(commit_id).strip()
        if not normalized_commit_id:
            raise ValueError("evidence-commit authority requires commit_id")
        idempotency_key = (
            "evidence-commit:"
            + canonical_payload_hash({"commit_id": normalized_commit_id})
        )
        with self._write_lock:
            row = self._conn.execute(
                """SELECT c.event_id AS claim_event_id,
                          c.source AS claim_source,
                          c.payload_sha256 AS claim_payload_sha256,
                          e.source AS event_source,
                          e.kind AS event_kind,
                          e.canonical_payload_hash AS event_payload_sha256
                     FROM event_idempotency_claims AS c
                     JOIN events AS e
                       ON e.run_id=c.run_id
                      AND e.event_id=c.event_id
                    WHERE c.run_id=?
                      AND c.kind=?
                      AND c.idempotency_key=?""",
                (
                    str(run_id),
                    EVIDENCE_COMMIT_EVENT_KIND,
                    idempotency_key,
                ),
            ).fetchone()
        if (
            row is None
            or int(row["claim_event_id"]) != int(event_id)
            or str(row["claim_source"]) != EVIDENCE_COMMIT_EVENT_SOURCE
            or str(row["event_source"]) != EVIDENCE_COMMIT_EVENT_SOURCE
            or str(row["event_kind"]) != EVIDENCE_COMMIT_EVENT_KIND
            or str(row["claim_payload_sha256"])
            != str(row["event_payload_sha256"])
        ):
            raise RuntimeError(
                "evidence-commit event lacks its exact authority claim"
            )

    def write_event_once(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
        ts: int | None = None,
        _reserved_capability: object | None = None,
    ) -> int:
        """Append one exact event or return its existing durable identity.

        The claim row and event share one transaction, so concurrent processes
        cannot both publish the same logical event. Reusing a key with changed
        source or payload is an authority discrepancy, not an idempotent retry.
        """
        assert_generic_event_kind_allowed(kind)
        if str(kind) == EVIDENCE_COMMIT_EVENT_KIND:
            if (
                _reserved_capability
                is not self.__evidence_commit_write_capability
            ):
                raise PermissionError(
                    "invalid evidence-commit write capability"
                )
            if str(source) != EVIDENCE_COMMIT_EVENT_SOURCE:
                raise ValueError(
                    "evidence-commit event requires its dedicated source"
                )
        else:
            assert_public_event_kind_allowed(kind)
        normalized_key = str(idempotency_key).strip()
        if not normalized_key:
            raise ValueError("event idempotency_key must be non-empty")
        if len(normalized_key.encode("utf-8")) > 512:
            raise ValueError("event idempotency_key exceeds 512 bytes")
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                event_payload = self._event_payload(
                    run_id=run_id,
                    source=source,
                    kind=kind,
                    payload=payload,
                )
                payload_sha256 = canonical_payload_hash(event_payload)
                existing = self._conn.execute(
                    """SELECT event_id, source, payload_sha256
                         FROM event_idempotency_claims
                        WHERE run_id=? AND kind=? AND idempotency_key=?""",
                    (str(run_id), str(kind), normalized_key),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["source"]) != str(source)
                        or str(existing["payload_sha256"]) != payload_sha256
                    ):
                        raise RuntimeError(
                            "event idempotency key was reused with changed "
                            "source or payload"
                        )
                    event_id = int(existing["event_id"])
                    event = self._conn.execute(
                        """SELECT source, canonical_payload_hash
                             FROM events
                            WHERE run_id=? AND event_id=? AND kind=?""",
                        (str(run_id), event_id, str(kind)),
                    ).fetchone()
                    if event is None:
                        raise RuntimeError(
                            "event idempotency claim references a missing event"
                        )
                    if (
                        str(event["source"]) != str(existing["source"])
                        or str(event["canonical_payload_hash"])
                        != str(existing["payload_sha256"])
                    ):
                        raise RuntimeError(
                            "event idempotency claim does not match its "
                            "immutable event"
                        )
                else:
                    event_id = self._insert_event_unlocked(
                        run_id=run_id,
                        source=source,
                        kind=kind,
                        payload=event_payload,
                        ts=ts,
                    )
                    self._conn.execute(
                        """INSERT INTO event_idempotency_claims(
                             run_id, kind, idempotency_key, event_id, source,
                             payload_sha256, created_at)
                           VALUES(?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(run_id),
                            str(kind),
                            normalized_key,
                            event_id,
                            str(source),
                            payload_sha256,
                            int(time.time()),
                        ),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._coordinate_committed_event(
                run_id=run_id,
                event_id=event_id,
                event_kind=kind,
            )
            return event_id

    def write_historical_operation_event(
        self,
        *,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        ts: int | None = None,
    ) -> int:
        """Append one capability-owned historical-operation event."""
        if not str(kind).startswith(HISTORICAL_OPERATION_EVENT_PREFIX):
            raise ValueError(
                "dedicated historical writer only accepts historical events"
            )
        if str(kind) in HISTORICAL_OPERATION_OWNER_FENCED_EVENT_KINDS:
            raise ValueError(
                "historical requested and terminal events require "
                "owner-fenced state methods"
            )
        return self._write_event_internal(
            run_id=run_id,
            source=HISTORICAL_OPERATION_EVENT_SOURCE,
            kind=kind,
            payload=payload,
            ts=ts,
        )

    def _write_event_internal(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict[str, Any],
        ts: int | None,
    ) -> int:
        assert_generic_event_kind_allowed(kind)
        assert_historical_operation_event_source(
            source=source,
            kind=kind,
        )
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
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
                    ts=ts,
                )
                self._conn.commit()
                self._coordinate_committed_event(
                    run_id=run_id,
                    event_id=event_id,
                    event_kind=kind,
                )
                return event_id
            except Exception:
                self._conn.rollback()
                raise

    def write_event_and_tail_offset(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict,
        path: str,
        byte_offset: int,
        ts: int | None = None,
    ) -> int:
        """Append an event and advance its rollout tail offset in one commit."""
        assert_generic_event_kind_allowed(kind)
        assert_public_event_kind_allowed(kind)
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
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
                    ts=ts,
                )
                self._set_tail_offset_unlocked(path, byte_offset)
                self._conn.commit()
                self._coordinate_committed_event(
                    run_id=run_id,
                    event_id=event_id,
                    event_kind=kind,
                )
                return event_id
            except Exception:
                self._conn.rollback()
                raise

    def ingest_source_line(
        self,
        *,
        run_id: str,
        source: str,
        path: str,
        start_offset: int,
        end_offset: int,
        raw_line: bytes,
        events: Sequence[tuple[str, dict[str, Any]]] = (),
        terminal_status: str | None = None,
        terminal_event_kind: str | None = None,
        decision: Decision | None = None,
        dead_letter: Mapping[str, Any] | None = None,
    ) -> SourceLineIngestion:
        """Commit one source line as an indivisible, replay-safe unit.

        All normalized events from the line, the durable tail offset, optional
        run closure, and optional outbox decision share one SQLite transaction.
        A malformed line is stored as a durable dead letter in the same table
        before its offset advances.
        """

        normalized_path = str(path)
        normalized_source = str(source)
        line_start = int(start_offset)
        line_end = int(end_offset)
        if line_start < 0 or line_end <= line_start:
            raise ValueError("source line offsets must describe a non-empty line")
        if len(raw_line) != line_end - line_start:
            raise ValueError("source line byte length does not match offsets")
        raw_sha256 = hashlib.sha256(raw_line).hexdigest()
        source_line_id = hashlib.sha256(
            (
                f"supervisor-source-line/v1\0{normalized_path}\0"
                f"{line_start}\0{line_end}\0{raw_sha256}"
            ).encode("utf-8")
        ).hexdigest()
        if dead_letter is not None and events:
            raise ValueError("dead-letter source lines cannot contain events")
        if (terminal_status is None) != (terminal_event_kind is None):
            raise ValueError(
                "terminal status and terminal event kind must be supplied together"
            )
        if decision is not None and terminal_status is None:
            raise ValueError("source-line decisions require a terminal transition")

        event_ids: list[int] = []
        status = (
            "dead_letter"
            if dead_letter is not None
            else "ingested" if events else "ignored"
        )
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    """SELECT source_line_id, raw_sha256, run_id, source,
                              status, event_ids_json
                         FROM source_line_ingestions
                        WHERE path=? AND start_offset=? AND end_offset=?""",
                    (normalized_path, line_start, line_end),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["source_line_id"]) != source_line_id
                        or str(existing["raw_sha256"]) != raw_sha256
                        or str(existing["run_id"]) != str(run_id)
                        or str(existing["source"]) != normalized_source
                    ):
                        raise RuntimeError(
                            "source line identity changed after durable ingestion"
                        )
                    event_ids = [
                        int(value)
                        for value in json.loads(existing["event_ids_json"])
                    ]
                    self._set_tail_offset_unlocked(
                        normalized_path,
                        max(
                            line_end,
                            self._tail_offset_unlocked(normalized_path),
                        ),
                    )
                    self._conn.commit()
                    result = SourceLineIngestion(
                        source_line_id=source_line_id,
                        event_ids=tuple(event_ids),
                        inserted=False,
                        status=str(existing["status"]),
                    )
                else:
                    durable_offset = self._tail_offset_unlocked(normalized_path)
                    if durable_offset != line_start:
                        raise RuntimeError(
                            "source line offset is not the durable next offset: "
                            f"path={normalized_path!r} expected={durable_offset} "
                            f"observed={line_start}"
                        )
                    for kind, payload in events:
                        assert_generic_event_kind_allowed(kind)
                        assert_public_event_kind_allowed(kind)
                        event_payload = self._event_payload(
                            run_id=run_id,
                            source=normalized_source,
                            kind=kind,
                            payload=payload,
                        )
                        event_ids.append(
                            self._insert_event_unlocked(
                                run_id=run_id,
                                source=normalized_source,
                                kind=kind,
                                payload=event_payload,
                            )
                        )
                    if terminal_status is not None:
                        changed = self._conn.execute(
                            """UPDATE runs
                                  SET ended_at=?, status=?
                                WHERE run_id=? AND status='running'""",
                            (int(time.time()), terminal_status, run_id),
                        ).rowcount
                        if changed and decision is not None:
                            self._enqueue_decision_unlocked(
                                decision,
                                idempotency_key=(
                                    f"terminal-evaluation:{run_id}"
                                ),
                            )
                    self._conn.execute(
                        """INSERT INTO source_line_ingestions(
                             source_line_id, path, start_offset, end_offset,
                             raw_sha256, run_id, source, status,
                             event_ids_json, raw_line_b64, error_json, created_at)
                           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            source_line_id,
                            normalized_path,
                            line_start,
                            line_end,
                            raw_sha256,
                            str(run_id),
                            normalized_source,
                            status,
                            json.dumps(event_ids, separators=(",", ":")),
                            (
                                base64.b64encode(raw_line).decode("ascii")
                                if dead_letter is not None
                                else None
                            ),
                            (
                                json.dumps(
                                    dict(dead_letter),
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    ensure_ascii=True,
                                    default=str,
                                )
                                if dead_letter is not None
                                else None
                            ),
                            int(time.time()),
                        ),
                    )
                    self._set_tail_offset_unlocked(normalized_path, line_end)
                    self._conn.commit()
                    result = SourceLineIngestion(
                        source_line_id=source_line_id,
                        event_ids=tuple(event_ids),
                        inserted=True,
                        status=status,
                    )
            except Exception:
                self._conn.rollback()
                raise

        for event_id, (kind, _payload) in zip(event_ids, events):
            self._coordinate_committed_event(
                run_id=run_id,
                event_id=event_id,
                event_kind=kind,
            )
        if decision is not None:
            self._decision_wakeup.set()
        return result

    def _tail_offset_unlocked(self, path: str) -> int:
        row = self._conn.execute(
            "SELECT byte_offset FROM tail_offsets WHERE path=?",
            (path,),
        ).fetchone()
        return int(row["byte_offset"]) if row is not None else 0

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
        with self._write_lock:
            rows = self._conn.execute(
                """SELECT event_id, run_id, event_sequence, ts, source, kind,
                          payload_json,
                          previous_event_hash, event_hash,
                          canonical_payload_hash, artifact_manifest_hash,
                          ledger_genesis_kind
                   FROM events
                   WHERE run_id=? AND event_id > ?
                   ORDER BY event_id ASC
                   LIMIT ?""",
                (run_id, cursor, page_limit),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "run_id": row["run_id"],
                "event_sequence": int(row["event_sequence"]),
                "ts": int(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "previous_event_hash": row["previous_event_hash"],
                "event_hash": row["event_hash"],
                "canonical_payload_hash": row["canonical_payload_hash"],
                "artifact_manifest_hash": row["artifact_manifest_hash"],
                "ledger_genesis_kind": row["ledger_genesis_kind"],
            }
            for row in rows
        ]

    def verify_event_ledger(
        self,
        run_id: str,
        *,
        checkpoint_store: Any | None = None,
        verifier: Any | None = None,
        trusted_latest_checkpoint: Mapping[str, Any] | None = None,
    ) -> LedgerVerification:
        """Verify release evidence against an externally pinned signed head.

        Missing authority inputs fail closed. Use
        :meth:`verify_event_ledger_structure` for diagnostic validation of the
        locally observed prefix.
        """
        rows = self._event_ledger_rows(run_id)
        if (
            checkpoint_store is None
            and verifier is None
            and trusted_latest_checkpoint is None
            and self._ledger_checkpoint_coordinator is not None
        ):
            return self._ledger_checkpoint_coordinator.verify(
                rows,
                expected_run_id=run_id,
            )
        if (
            checkpoint_store is None
            or verifier is None
        ):
            return verify_event_chain(
                rows,
                expected_run_id=run_id,
            )
        from .ledger_checkpoints import verify_authoritative_event_chain

        return verify_authoritative_event_chain(
            rows,
            expected_run_id=run_id,
            checkpoint_store=checkpoint_store,
            verifier=verifier,
            trusted_latest_checkpoint=trusted_latest_checkpoint,
        )

    def verify_event_ledger_structure(
        self,
        run_id: str,
    ) -> LedgerVerification:
        """Verify the observed local prefix without claiming tail completeness."""
        rows = self._event_ledger_rows(run_id)
        return verify_event_chain_structure(
            rows,
            expected_run_id=run_id,
        )

    def checkpoint_event_ledger(
        self,
        run_id: str,
        *,
        checkpoint_store: Any,
        signer: Any,
        verifier: Any,
        created_at: int | None = None,
    ) -> Any:
        verification = self.verify_event_ledger_structure(run_id)
        if (
            not verification.valid
            or verification.event_count <= 0
            or verification.head_event_id is None
            or verification.head_event_hash is None
        ):
            raise RuntimeError(
                "cannot checkpoint an empty or invalid event ledger"
            )
        return checkpoint_store.append_signed_head(
            run_id=run_id,
            head_event_id=verification.head_event_id,
            head_event_hash=verification.head_event_hash,
            event_count=verification.event_count,
            signer=signer,
            verifier=verifier,
            created_at=(
                int(time.time())
                if created_at is None
                else int(created_at)
            ),
        )

    def verify_event_ledger_authoritatively(
        self,
        run_id: str,
        *,
        checkpoint_store: Any,
        verifier: Any,
        trusted_latest_checkpoint: dict[str, Any] | None = None,
    ) -> LedgerVerification:
        """Compatibility alias for the release-grade verification boundary."""
        return self.verify_event_ledger(
            run_id,
            checkpoint_store=checkpoint_store,
            verifier=verifier,
            trusted_latest_checkpoint=trusted_latest_checkpoint,
        )

    def read_dual_agent_gate_events(self, run_id: str) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """SELECT event_id, ts, kind, payload_json
               FROM events
               WHERE run_id=?
                 AND kind IN (
                   'dual_agent_gate_round',
                   'dual_agent_gate_result',
                   'dual_agent_planning_validation',
                   'dual_agent_production_trace_failed',
                   'dual_agent_production_trace_recorded',
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
                   'supervisor_cross_vendor_review_selected',
                   'supervisor_degraded_review_unavailable',
                   'supervisor_evidence_attempt_recorded',
                   'supervisor_lesson_injection',
                   'supervisor_lesson_recorded',
                   'supervisor_policy_overlay_snapshot',
                   'supervisor_review_context_validation',
                   'supervisor_review_packet_created',
                   'supervisor_worker_blocked',
                   'supervisor_worker_cancelled',
                   'supervisor_worker_completed',
                   'supervisor_worker_dispatched',
                   'supervisor_worker_failed',
                   'supervisor_worker_roster_checked',
                   'supervisor_worker_session_created',
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
            self._conn.execute("BEGIN IMMEDIATE")
            try:
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
                    """SELECT * FROM supervisor_quality_trends
                        WHERE run_id=? AND gate=?""",
                    (run_id, gate),
                ).fetchone()
                if row is None:
                    raise RuntimeError("quality trend row was not persisted")
                projection_row = _quality_trend_row_to_dict(row)
                projection_row.pop("id", None)
                event_id = self._insert_event_unlocked(
                    run_id=run_id,
                    source="quality_trends",
                    kind=QUALITY_TREND_PROJECTION_EVENT,
                    payload=self._event_payload(
                        run_id=run_id,
                        source="quality_trends",
                        kind=QUALITY_TREND_PROJECTION_EVENT,
                        payload=quality_trend_projection_event_payload(
                            projection_row
                        ),
                    ),
                )
                self._conn.commit()
                self._coordinate_committed_event(
                    run_id=run_id,
                    event_id=event_id,
                    event_kind=QUALITY_TREND_PROJECTION_EVENT,
                )
                return _quality_trend_row_to_dict(row)
            except Exception:
                self._conn.rollback()
                raise

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
        sample, false_count, denominator, rate = validate_quality_audit_counts(
            sample_size=sample_size,
            false_accept_count=false_accept_count,
            false_accept_denominator=false_accept_denominator,
        )
        with self._write_lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                existing = self._conn.execute(
                    "SELECT details_json FROM supervisor_quality_trends WHERE run_id=? AND gate=?",
                    (run_id, gate),
                ).fetchone()
                if existing is None:
                    self._conn.commit()
                    return None
                latest = self._conn.execute(
                    """SELECT computed_at
                         FROM quality_trend_audits
                        WHERE run_id=? AND gate=?
                        ORDER BY computed_at DESC
                        LIMIT 1""",
                    (run_id, gate),
                ).fetchone()
                latest_computed_at = int(latest["computed_at"]) if latest is not None else -1
                computed_at = max(int(time.time()), latest_computed_at + 1)
                audit_details_json = json.dumps(redact(audit_details or {}), sort_keys=True)
                self._conn.execute(
                    """INSERT INTO quality_trend_audits(
                         run_id, gate, sample_size, false_accept_count,
                         false_accept_denominator, false_accept_rate,
                         audit_details_json, computed_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        gate,
                        sample,
                        false_count,
                        denominator,
                        rate,
                        audit_details_json,
                        computed_at,
                    ),
                )
                try:
                    details = json.loads(existing["details_json"] or "{}")
                except json.JSONDecodeError:
                    details = {}
                details["p11_audit"] = json.loads(audit_details_json)
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
                        sample,
                        false_count,
                        denominator,
                        rate,
                        json.dumps(details, sort_keys=True),
                        computed_at,
                        run_id,
                        gate,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM supervisor_quality_trends WHERE run_id=? AND gate=?",
                    (run_id, gate),
                ).fetchone()
                event_id = 0
                if row is not None:
                    projection_row = _quality_trend_row_to_dict(row)
                    projection_row.pop("id", None)
                    event_id = self._insert_event_unlocked(
                        run_id=run_id,
                        source="quality_trends",
                        kind=QUALITY_TREND_PROJECTION_EVENT,
                        payload=self._event_payload(
                            run_id=run_id,
                            source="quality_trends",
                            kind=QUALITY_TREND_PROJECTION_EVENT,
                            payload=quality_trend_projection_event_payload(
                                projection_row
                            ),
                        ),
                    )
                self._conn.commit()
                self._coordinate_committed_event(
                    run_id=run_id,
                    event_id=event_id,
                    event_kind=QUALITY_TREND_PROJECTION_EVENT,
                )
                return _quality_trend_row_to_dict(row) if row is not None else None
            except Exception:
                self._conn.rollback()
                raise

    def list_quality_trend_audits(
        self,
        *,
        run_id: str | None = None,
        gate: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        if gate:
            clauses.append("gate=?")
            params.append(gate)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT *
                  FROM quality_trend_audits
                  {where}
                 ORDER BY computed_at ASC, run_id ASC, gate ASC""",
            tuple(params),
        ).fetchall()
        return [_quality_trend_audit_row_to_dict(row) for row in rows]

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

    def quality_trend_projection_snapshot(self) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        for row in self.list_quality_trend_rows():
            normalized = dict(row)
            normalized.pop("id", None)
            snapshot.append(
                canonical_quality_trend_projection_row(normalized)
            )
        return sorted(
            snapshot,
            key=lambda item: (item["run_id"], item["gate"]),
        )

    def rebuild_quality_trend_projection_from_ledger(
        self,
        *,
        replace: bool = False,
        checkpoint_store: Any | None = None,
        verifier: Any | None = None,
        expected_stream_checkpoint_pins: (
            Mapping[str, Mapping[str, Any]] | None
        ) = None,
    ) -> list[dict[str, Any]]:
        if (
            checkpoint_store is None
            or verifier is None
            or expected_stream_checkpoint_pins is None
        ):
            raise RuntimeError(
                "quality trend projection rebuild requires authoritative "
                "checkpoint pins for expected streams"
            )
        from .ledger_checkpoints import (
            normalize_checkpoint_identity,
            verify_authoritative_event_chain,
        )

        expected_pins: dict[str, dict[str, Any]] = {}
        for raw_run_id, raw_identity in (
            expected_stream_checkpoint_pins.items()
        ):
            run_id = str(raw_run_id).strip()
            if not run_id or run_id in expected_pins:
                raise RuntimeError(
                    "quality trend expected stream inventory is invalid"
                )
            try:
                identity = normalize_checkpoint_identity(raw_identity)
            except Exception as exc:
                raise RuntimeError(
                    "quality trend expected checkpoint identity is invalid "
                    f"for {run_id}"
                ) from exc
            if str(identity["run_id"]) != run_id:
                raise RuntimeError(
                    "quality trend expected checkpoint run_id mismatch for "
                    f"{run_id}"
                )
            expected_pins[run_id] = identity
        if not expected_pins:
            raise RuntimeError(
                "quality trend expected stream inventory must be non-empty"
            )

        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                run_rows = self._conn.execute(
                    """SELECT DISTINCT run_id
                         FROM events
                        WHERE kind=?
                        ORDER BY run_id ASC""",
                    (QUALITY_TREND_PROJECTION_EVENT,),
                ).fetchall()
                observed_run_ids = {
                    str(run_row["run_id"])
                    for run_row in run_rows
                }
                unexpected = sorted(
                    observed_run_ids - set(expected_pins)
                )
                if unexpected:
                    raise RuntimeError(
                        "quality trend ledger contains streams absent from "
                        "the expected inventory: "
                        + ", ".join(unexpected)
                    )
                events: list[dict[str, Any]] = []
                for run_id in sorted(expected_pins):
                    rows = self._conn.execute(
                        """SELECT event_id, run_id, event_sequence, ts,
                                  source, kind, payload_json,
                                  previous_event_hash, event_hash,
                                  canonical_payload_hash,
                                  artifact_manifest_hash,
                                  ledger_genesis_kind
                             FROM events
                            WHERE run_id=?
                            ORDER BY event_sequence ASC""",
                        (run_id,),
                    ).fetchall()
                    verification = verify_authoritative_event_chain(
                        rows,
                        expected_run_id=run_id,
                        checkpoint_store=checkpoint_store,
                        verifier=verifier,
                        trusted_latest_checkpoint=expected_pins[run_id],
                    )
                    if not verification.valid:
                        raise RuntimeError(
                            "quality trend ledger verification failed for "
                            f"{run_id}: {verification.failure_code}"
                        )
                    if not any(
                        str(row["kind"])
                        == QUALITY_TREND_PROJECTION_EVENT
                        for row in rows
                    ):
                        raise RuntimeError(
                            "quality trend expected stream contains no "
                            f"projection event: {run_id}"
                        )
                    events.extend(
                        {
                            "run_id": run_id,
                            "source": row["source"],
                            "kind": row["kind"],
                            "payload": json.loads(
                                str(row["payload_json"])
                            ),
                        }
                        for row in rows
                    )
                rebuilt = rebuild_quality_trend_projection(events)
                if replace:
                    self._conn.execute(
                        "DELETE FROM supervisor_quality_trends"
                    )
                    for row in rebuilt:
                        self._conn.execute(
                            """INSERT INTO supervisor_quality_trends(
                                 run_id, task_id, task_class, gate, accepted,
                                 first_pass_accepted, revision_rounds,
                                 time_to_accepted_outcome_s,
                                 p11_audit_sample_size, false_accept_count,
                                 false_accept_denominator, false_accept_rate,
                                 policy_overlay_hash, policy_proposal_id,
                                 details_json, computed_at)
                               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                row["run_id"],
                                row["task_id"],
                                row["task_class"],
                                row["gate"],
                                1 if row["accepted"] else 0,
                                1 if row["first_pass_accepted"] else 0,
                                row["revision_rounds"],
                                row["time_to_accepted_outcome_s"],
                                row["p11_audit_sample_size"],
                                row["false_accept_count"],
                                row["false_accept_denominator"],
                                row["false_accept_rate"],
                                row["policy_overlay_hash"],
                                row["policy_proposal_id"],
                                json.dumps(
                                    row["details"],
                                    sort_keys=True,
                                ),
                                row["computed_at"],
                            ),
                        )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return rebuilt

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

    def park_autoresearch_experiment(
        self,
        *,
        experiment_id: str,
        parked_at: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time()) if parked_at is None else int(parked_at)
        with self._write_lock:
            row = self._conn.execute(
                """SELECT * FROM supervisor_autoresearch_experiments
                   WHERE experiment_id=?""",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
            if row["status"] in {"draft", "runnable"}:
                self._conn.execute(
                    """UPDATE supervisor_autoresearch_experiments
                          SET status='parked',
                              updated_at=?
                        WHERE experiment_id=?""",
                    (now, experiment_id),
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

    # --- historical evaluation operation coordination ---
    def reserve_historical_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        operation: str,
    ) -> tuple[dict[str, Any], bool]:
        """Claim one durable operation identity without executing under a DB lock."""
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = int(time.time())
                existing = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if existing is not None:
                    self._conn.commit()
                    return dict(existing), False
                try:
                    self._conn.execute(
                        """INSERT INTO historical_operation_claims(
                               operation_id, request_hash, operation, status,
                               terminal_event_id, created_at, updated_at)
                           VALUES(?, ?, ?, 'running', NULL, ?, ?)""",
                        (
                            operation_id,
                            request_hash,
                            operation,
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError:
                    existing = self._conn.execute(
                        """SELECT * FROM historical_operation_claims
                           WHERE operation_id=?""",
                        (operation_id,),
                    ).fetchone()
                    if existing is None:
                        raise
                    self._conn.commit()
                    return dict(existing), False
                row = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        "historical operation reservation was not persisted"
                    )
                self._conn.commit()
                return dict(row), True
            except Exception:
                self._conn.rollback()
                raise

    def claim_historical_operation_execution(
        self,
        *,
        operation_id: str,
        request_hash: str,
        operation: str,
        request: dict[str, Any],
        owner_token: str,
        expected_claim_updated_at: Any,
        expected_execution_owner_token: Any,
        expected_execution_generation: Any,
        expected_execution_heartbeat_at: Any,
        lease_duration_s: float | None = None,
    ) -> tuple[dict[str, Any], int | None, bool]:
        """Atomically append requested while claiming the side-effect boundary."""
        normalized_owner_token = str(owner_token).strip()
        if not normalized_owner_token:
            raise ValueError(
                "historical operation execution owner token is required"
            )
        normalized_lease_duration = (
            None
            if lease_duration_s is None
            else _historical_lease_duration_seconds(lease_duration_s)
        )
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                lease_cutoff = (
                    None
                    if normalized_lease_duration is None
                    else now - normalized_lease_duration
                )
                claim = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if claim is None:
                    raise KeyError(
                        f"historical operation not found: {operation_id}"
                    )
                requested_events = self._conn.execute(
                    """SELECT event_id
                         FROM events
                        WHERE run_id=?
                          AND kind='historical_operation.requested'
                        ORDER BY event_id ASC
                        LIMIT 2""",
                    (operation_id,),
                ).fetchall()
                requested_event_id = (
                    int(requested_events[0]["event_id"])
                    if requested_events
                    else None
                )
                claim_dict = dict(claim)
                may_claim = (
                    str(claim["request_hash"]) == request_hash
                    and str(claim["operation"]) == operation
                    and str(claim["status"]) == "running"
                    and claim["terminal_event_id"] is None
                    and claim["updated_at"] == expected_claim_updated_at
                    and (
                        claim["execution_owner_token"]
                        == expected_execution_owner_token
                    )
                    and (
                        claim["execution_generation"]
                        == expected_execution_generation
                    )
                    and (
                        claim["execution_heartbeat_at"]
                        == expected_execution_heartbeat_at
                    )
                    and requested_event_id is None
                    and (
                        lease_cutoff is None
                        or (
                            claim["execution_owner_token"] is None
                            and int(
                                claim["execution_generation"] or 0
                            )
                            == 0
                            and claim["execution_heartbeat_at"] is None
                            and _historical_lease_is_expired(
                                claim["updated_at"],
                                state_now=now,
                                lease_duration_s=(
                                    normalized_lease_duration
                                ),
                            )
                        )
                    )
                )
                if not may_claim:
                    self._conn.commit()
                    return claim_dict, requested_event_id, False
                cursor = self._conn.execute(
                    """UPDATE historical_operation_claims
                          SET execution_owner_token=?,
                              execution_generation=execution_generation + 1,
                              execution_heartbeat_at=?,
                              updated_at=?
                        WHERE operation_id=?
                          AND request_hash=?
                          AND operation=?
                          AND status='running'
                          AND terminal_event_id IS NULL
                          AND updated_at IS ?
                          AND execution_owner_token IS ?
                          AND execution_generation=?
                          AND execution_heartbeat_at IS ?
                          AND (
                            ? IS NULL
                            OR (
                              execution_owner_token IS NULL
                              AND execution_generation=0
                              AND execution_heartbeat_at IS NULL
                              AND typeof(updated_at) IN ('integer', 'real')
                              AND updated_at<=?
                            )
                          )""",
                    (
                        normalized_owner_token,
                        now,
                        int(now),
                        operation_id,
                        request_hash,
                        operation,
                        expected_claim_updated_at,
                        expected_execution_owner_token,
                        expected_execution_generation,
                        expected_execution_heartbeat_at,
                        lease_cutoff,
                        lease_cutoff,
                    ),
                )
                if cursor.rowcount != 1:
                    current = self._conn.execute(
                        """SELECT * FROM historical_operation_claims
                           WHERE operation_id=?""",
                        (operation_id,),
                    ).fetchone()
                    if current is None:
                        raise KeyError(
                            f"historical operation not found: {operation_id}"
                        )
                    self._conn.commit()
                    return dict(current), None, False
                claimed = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if claimed is None:
                    raise RuntimeError(
                        "historical operation execution claim disappeared"
                    )
                payload = {
                    "operation_id": operation_id,
                    "request_hash": request_hash,
                    "request": request,
                    "execution_owner_token": normalized_owner_token,
                    "execution_generation": int(
                        claimed["execution_generation"]
                    ),
                }
                prepared_payload = self._event_payload(
                    run_id=operation_id,
                    source=HISTORICAL_OPERATION_EVENT_SOURCE,
                    kind="historical_operation.requested",
                    payload=payload,
                )
                requested_event_id = self._insert_event_unlocked(
                    run_id=operation_id,
                    source=HISTORICAL_OPERATION_EVENT_SOURCE,
                    kind="historical_operation.requested",
                    payload=prepared_payload,
                )
                self._conn.commit()
                self._coordinate_committed_event(
                    run_id=operation_id,
                    event_id=requested_event_id,
                    event_kind="historical_operation.requested",
                )
                return dict(claimed), requested_event_id, True
            except Exception:
                self._conn.rollback()
                raise

    def historical_operation_preflight_claim_is_stale(
        self,
        *,
        operation_id: str,
        request_hash: str,
        operation: str,
        expected_claim_updated_at: Any,
        expected_execution_owner_token: Any,
        expected_execution_generation: Any,
        expected_execution_heartbeat_at: Any,
        lease_duration_s: float,
    ) -> bool:
        """Check an exact pre-side-effect lease against the state-local clock."""
        normalized_lease_duration = _historical_lease_duration_seconds(
            lease_duration_s
        )
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                state_now = time.time()
                claim = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if claim is None:
                    raise KeyError(
                        f"historical operation not found: {operation_id}"
                    )
                side_effect_event = self._conn.execute(
                    """SELECT event_id
                         FROM events
                        WHERE run_id=?
                          AND kind IN (
                            'historical_operation.requested',
                            'historical_operation.completed',
                            'historical_operation.failed'
                          )
                        LIMIT 1""",
                    (operation_id,),
                ).fetchone()
                stale = (
                    str(claim["request_hash"]) == request_hash
                    and str(claim["operation"]) == operation
                    and str(claim["status"]) == "running"
                    and claim["terminal_event_id"] is None
                    and claim["updated_at"] == expected_claim_updated_at
                    and claim["execution_owner_token"] is None
                    and expected_execution_owner_token is None
                    and (
                        claim["execution_generation"]
                        == expected_execution_generation
                    )
                    and int(claim["execution_generation"] or 0) == 0
                    and claim["execution_heartbeat_at"] is None
                    and expected_execution_heartbeat_at is None
                    and side_effect_event is None
                    and _historical_lease_is_expired(
                        claim["updated_at"],
                        state_now=state_now,
                        lease_duration_s=normalized_lease_duration,
                    )
                )
                self._conn.commit()
                return stale
            except Exception:
                self._conn.rollback()
                raise

    def release_historical_operation_preflight(
        self,
        *,
        operation_id: str,
        request_hash: str,
        operation: str,
        expected_claim_updated_at: Any,
        expected_execution_owner_token: Any,
        expected_execution_generation: Any,
        expected_execution_heartbeat_at: Any,
        payload: dict[str, Any],
    ) -> int | None:
        """Append a retry release only while the observed preflight still owns."""
        if (
            str(payload.get("operation_id") or "") != operation_id
            or str(payload.get("request_hash") or "") != request_hash
            or str(payload.get("operation") or "") != operation
        ):
            raise ValueError(
                "historical preflight release payload does not match its claim"
            )
        committed_event_id: int | None = None
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                claim = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if claim is None:
                    raise KeyError(
                        f"historical operation not found: {operation_id}"
                    )
                side_effect_event = self._conn.execute(
                    """SELECT event_id
                         FROM events
                        WHERE run_id=?
                          AND kind IN (
                            'historical_operation.requested',
                            'historical_operation.completed',
                            'historical_operation.failed'
                          )
                        LIMIT 1""",
                    (operation_id,),
                ).fetchone()
                may_release = (
                    str(claim["request_hash"]) == request_hash
                    and str(claim["operation"]) == operation
                    and str(claim["status"]) == "running"
                    and claim["terminal_event_id"] is None
                    and claim["updated_at"] == expected_claim_updated_at
                    and claim["execution_owner_token"] is None
                    and expected_execution_owner_token is None
                    and (
                        claim["execution_generation"]
                        == expected_execution_generation
                    )
                    and int(claim["execution_generation"] or 0) == 0
                    and claim["execution_heartbeat_at"] is None
                    and expected_execution_heartbeat_at is None
                    and side_effect_event is None
                )
                if not may_release:
                    self._conn.commit()
                    return None
                now = int(time.time())
                try:
                    next_updated_at = max(
                        now,
                        int(float(claim["updated_at"])) + 1,
                    )
                except (TypeError, ValueError):
                    next_updated_at = now
                cursor = self._conn.execute(
                    """UPDATE historical_operation_claims
                          SET updated_at=?
                        WHERE operation_id=?
                          AND request_hash=?
                          AND operation=?
                          AND status='running'
                          AND terminal_event_id IS NULL
                          AND updated_at IS ?
                          AND execution_owner_token IS NULL
                          AND execution_generation=0
                          AND execution_heartbeat_at IS NULL""",
                    (
                        next_updated_at,
                        operation_id,
                        request_hash,
                        operation,
                        expected_claim_updated_at,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.commit()
                    return None
                prepared_payload = self._event_payload(
                    run_id=operation_id,
                    source=HISTORICAL_OPERATION_EVENT_SOURCE,
                    kind=(
                        "historical_operation.preflight_released"
                    ),
                    payload=payload,
                )
                committed_event_id = self._insert_event_unlocked(
                    run_id=operation_id,
                    source=HISTORICAL_OPERATION_EVENT_SOURCE,
                    kind="historical_operation.preflight_released",
                    payload=prepared_payload,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if committed_event_id is not None:
            self._coordinate_committed_event(
                run_id=operation_id,
                event_id=committed_event_id,
                event_kind="historical_operation.preflight_released",
            )
        return committed_event_id

    def heartbeat_historical_operation_execution(
        self,
        *,
        operation_id: str,
        request_hash: str,
        owner_token: str,
        execution_generation: int,
    ) -> bool:
        """Refresh a running execution lease only for its persisted owner."""
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                cursor = self._conn.execute(
                    """UPDATE historical_operation_claims
                          SET execution_heartbeat_at=?, updated_at=?
                        WHERE operation_id=?
                          AND request_hash=?
                          AND status='running'
                          AND terminal_event_id IS NULL
                          AND execution_owner_token=?
                          AND execution_generation=?""",
                    (
                        now,
                        int(now),
                        operation_id,
                        request_hash,
                        str(owner_token),
                        int(execution_generation),
                    ),
                )
                self._conn.commit()
                return cursor.rowcount == 1
            except Exception:
                self._conn.rollback()
                raise

    def take_over_stale_historical_operation_execution(
        self,
        *,
        operation_id: str,
        request_hash: str,
        operation: str,
        new_owner_token: str,
        expected_requested_event_id: int,
        expected_claim_updated_at: Any,
        expected_execution_owner_token: Any,
        expected_execution_generation: Any,
        expected_execution_heartbeat_at: Any,
        lease_duration_s: float,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically replace exactly the observed stale execution owner."""
        normalized_owner_token = str(new_owner_token).strip()
        if not normalized_owner_token:
            raise ValueError(
                "historical operation execution owner token is required"
            )
        normalized_lease_duration = _historical_lease_duration_seconds(
            lease_duration_s
        )
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                lease_cutoff = now - normalized_lease_duration
                claim = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if claim is None:
                    raise KeyError(
                        f"historical operation not found: {operation_id}"
                    )
                requested_events = self._conn.execute(
                    """SELECT event_id, source, payload_json
                         FROM events
                        WHERE run_id=?
                          AND kind='historical_operation.requested'
                        ORDER BY event_id ASC
                        LIMIT 2""",
                    (operation_id,),
                ).fetchall()
                requested_is_valid = False
                if len(requested_events) == 1:
                    requested_payload = _json_payload(
                        str(requested_events[0]["payload_json"])
                    )
                    requested_is_valid = (
                        int(requested_events[0]["event_id"])
                        == int(expected_requested_event_id)
                        and str(requested_events[0]["source"])
                        == HISTORICAL_OPERATION_EVENT_SOURCE
                        and str(
                            requested_payload.get("operation_id") or ""
                        )
                        == operation_id
                        and str(
                            requested_payload.get("request_hash") or ""
                        )
                        == request_hash
                    )
                heartbeat_value = claim["execution_heartbeat_at"]
                lease_value = (
                    claim["updated_at"]
                    if claim["execution_owner_token"] is None
                    and heartbeat_value is None
                    else heartbeat_value
                )
                lease_is_stale = False
                if not isinstance(lease_value, bool):
                    try:
                        lease_is_stale = (
                            float(lease_value) <= lease_cutoff
                        )
                    except (TypeError, ValueError):
                        lease_is_stale = False
                may_take_over = (
                    str(claim["request_hash"]) == request_hash
                    and str(claim["operation"]) == operation
                    and str(claim["status"]) == "running"
                    and claim["terminal_event_id"] is None
                    and claim["updated_at"] == expected_claim_updated_at
                    and (
                        claim["execution_owner_token"]
                        == expected_execution_owner_token
                    )
                    and (
                        claim["execution_generation"]
                        == expected_execution_generation
                    )
                    and (
                        heartbeat_value
                        == expected_execution_heartbeat_at
                    )
                    and requested_is_valid
                    and lease_is_stale
                )
                if not may_take_over:
                    self._conn.commit()
                    return dict(claim), False
                cursor = self._conn.execute(
                    """UPDATE historical_operation_claims
                          SET execution_owner_token=?,
                              execution_generation=execution_generation + 1,
                              execution_heartbeat_at=?,
                              updated_at=?
                        WHERE operation_id=?
                          AND request_hash=?
                          AND operation=?
                          AND status='running'
                          AND terminal_event_id IS NULL
                          AND updated_at IS ?
                          AND execution_owner_token IS ?
                          AND execution_generation=?
                          AND execution_heartbeat_at IS ?
                          AND (
                            (
                              execution_owner_token IS NULL
                              AND execution_heartbeat_at IS NULL
                              AND typeof(updated_at) IN ('integer', 'real')
                              AND updated_at<=?
                            )
                            OR (
                              execution_owner_token IS NOT NULL
                              AND execution_heartbeat_at IS NOT NULL
                              AND typeof(execution_heartbeat_at)
                                    IN ('integer', 'real')
                              AND execution_heartbeat_at<=?
                            )
                          )""",
                    (
                        normalized_owner_token,
                        now,
                        int(now),
                        operation_id,
                        request_hash,
                        operation,
                        expected_claim_updated_at,
                        expected_execution_owner_token,
                        expected_execution_generation,
                        expected_execution_heartbeat_at,
                        lease_cutoff,
                        lease_cutoff,
                    ),
                )
                current = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if current is None:
                    raise RuntimeError(
                        "historical operation execution claim disappeared"
                    )
                self._conn.commit()
                return dict(current), cursor.rowcount == 1
            except Exception:
                self._conn.rollback()
                raise

    def terminalize_historical_operation_execution(
        self,
        *,
        operation_id: str,
        request_hash: str,
        operation: str,
        owner_token: str,
        execution_generation: int,
        status: str,
        payload: dict[str, Any],
    ) -> tuple[int | None, bool]:
        """Atomically append a terminal event and fence its claim transition."""
        if status not in {"completed", "failed"}:
            raise ValueError(
                "historical operation terminal status must be completed or failed"
            )
        expected_kind = f"historical_operation.{status}"
        prepared_payload = self._event_payload(
            run_id=operation_id,
            source=HISTORICAL_OPERATION_EVENT_SOURCE,
            kind=expected_kind,
            payload=payload,
        )
        committed_event_id: int | None = None
        created = False
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = int(time.time())
                claim = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if claim is None:
                    raise KeyError(
                        f"historical operation not found: {operation_id}"
                    )
                if (
                    str(claim["request_hash"]) != request_hash
                    or str(claim["operation"]) != operation
                ):
                    raise RuntimeError(
                        "historical operation terminal identity does not "
                        "match its claim"
                    )
                if str(claim["status"]) != "running":
                    existing_event_id = int(
                        claim["terminal_event_id"] or 0
                    )
                    existing = self._conn.execute(
                        """SELECT source, kind, payload_json
                             FROM events
                            WHERE run_id=? AND event_id=?""",
                        (operation_id, existing_event_id),
                    ).fetchone()
                    if (
                        str(claim["status"]) == status
                        and existing is not None
                        and str(existing["source"])
                        == HISTORICAL_OPERATION_EVENT_SOURCE
                        and str(existing["kind"]) == expected_kind
                        and _json_payload(str(existing["payload_json"]))
                        == prepared_payload
                    ):
                        self._conn.commit()
                        committed_event_id = existing_event_id
                    else:
                        self._conn.commit()
                        return None, False
                elif (
                    str(claim["execution_owner_token"] or "")
                    != str(owner_token)
                    or int(claim["execution_generation"] or 0)
                    != int(execution_generation)
                    or claim["terminal_event_id"] is not None
                ):
                    self._conn.commit()
                    return None, False
                else:
                    try:
                        requested_event_id = int(
                            payload.get("requested_event_id")
                        )
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(
                            "historical operation terminal requested event "
                            "linkage is invalid"
                        ) from exc
                    requested_events = self._conn.execute(
                        """SELECT event_id, source, payload_json
                             FROM events
                            WHERE run_id=?
                              AND kind='historical_operation.requested'
                            ORDER BY event_id ASC
                            LIMIT 2""",
                        (operation_id,),
                    ).fetchall()
                    if len(requested_events) != 1:
                        raise RuntimeError(
                            "historical operation terminal requested event "
                            "linkage is invalid"
                        )
                    requested_payload = _json_payload(
                        str(requested_events[0]["payload_json"])
                    )
                    if (
                        int(requested_events[0]["event_id"])
                        != requested_event_id
                        or str(requested_events[0]["source"])
                        != HISTORICAL_OPERATION_EVENT_SOURCE
                        or str(
                            requested_payload.get("operation_id") or ""
                        )
                        != operation_id
                        or str(
                            requested_payload.get("request_hash") or ""
                        )
                        != request_hash
                    ):
                        raise RuntimeError(
                            "historical operation terminal requested event "
                            "linkage is invalid"
                        )
                    committed_event_id = self._insert_event_unlocked(
                        run_id=operation_id,
                        source=HISTORICAL_OPERATION_EVENT_SOURCE,
                        kind=expected_kind,
                        payload=prepared_payload,
                    )
                    cursor = self._conn.execute(
                        """UPDATE historical_operation_claims
                              SET status=?, terminal_event_id=?, updated_at=?
                            WHERE operation_id=?
                              AND request_hash=?
                              AND operation=?
                              AND status='running'
                              AND terminal_event_id IS NULL
                              AND execution_owner_token=?
                              AND execution_generation=?""",
                        (
                            status,
                            committed_event_id,
                            now,
                            operation_id,
                            request_hash,
                            operation,
                            str(owner_token),
                            int(execution_generation),
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            "historical operation terminal owner fence was lost"
                        )
                    created = True
                    self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if committed_event_id is None:
            return None, False
        self._coordinate_committed_event(
            run_id=operation_id,
            event_id=committed_event_id,
            event_kind=expected_kind,
        )
        return committed_event_id, created

    def complete_historical_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        status: str,
        terminal_event_id: int,
    ) -> int:
        """Compare-and-set one running coordination claim to a terminal state."""
        if status not in {"completed", "failed"}:
            raise ValueError(
                "historical operation terminal status must be completed or failed"
            )
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = int(time.time())
                claim = self._conn.execute(
                    """SELECT request_hash, operation
                         FROM historical_operation_claims
                        WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if claim is None:
                    raise KeyError(
                        f"historical operation not found: {operation_id}"
                    )
                expected_kind = f"historical_operation.{status}"
                event = self._conn.execute(
                    """SELECT source, kind, payload_json
                         FROM events
                        WHERE run_id=? AND event_id=?""",
                    (operation_id, int(terminal_event_id)),
                ).fetchone()
                if (
                    event is None
                    or str(event["source"])
                    != HISTORICAL_OPERATION_EVENT_SOURCE
                    or str(event["kind"]) != expected_kind
                ):
                    raise RuntimeError(
                        "historical operation terminal claim requires its "
                        "matching ledger event"
                    )
                payload = _json_payload(str(event["payload_json"]))
                if str(payload.get("request_hash") or "") != request_hash:
                    raise RuntimeError(
                        "historical operation terminal event request hash "
                        "does not match its claim"
                    )
                if (
                    str(payload.get("operation_id") or "")
                    != operation_id
                    or str(payload.get("operation") or "")
                    != str(claim["operation"])
                ):
                    raise RuntimeError(
                        "historical operation terminal event identity "
                        "does not match its claim"
                    )
                try:
                    requested_event_id = int(
                        payload.get("requested_event_id")
                    )
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "historical operation terminal requested event "
                        "linkage is invalid"
                    ) from exc
                requested = self._conn.execute(
                    """SELECT source, kind, payload_json
                         FROM events
                        WHERE run_id=? AND event_id=?""",
                    (operation_id, requested_event_id),
                ).fetchone()
                if (
                    requested is None
                    or requested_event_id >= int(terminal_event_id)
                    or str(requested["source"])
                    != HISTORICAL_OPERATION_EVENT_SOURCE
                    or str(requested["kind"])
                    != "historical_operation.requested"
                ):
                    raise RuntimeError(
                        "historical operation terminal requested event "
                        "linkage is invalid"
                    )
                requested_payload = _json_payload(
                    str(requested["payload_json"])
                )
                if (
                    str(requested_payload.get("operation_id") or "")
                    != operation_id
                    or str(requested_payload.get("request_hash") or "")
                    != request_hash
                ):
                    raise RuntimeError(
                        "historical operation terminal requested event "
                        "linkage is invalid"
                    )
                cursor = self._conn.execute(
                    """UPDATE historical_operation_claims
                          SET status=?, terminal_event_id=?, updated_at=?
                        WHERE operation_id=?
                          AND request_hash=?
                          AND status='running'
                          AND execution_owner_token IS NULL
                          AND execution_generation=0
                          AND execution_heartbeat_at IS NULL""",
                    (
                        status,
                        int(terminal_event_id),
                        now,
                        operation_id,
                        request_hash,
                    ),
                )
                if cursor.rowcount == 1:
                    self._conn.commit()
                    return 1
                row = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=?""",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(
                        f"historical operation not found: {operation_id}"
                    )
                if (
                    str(row["request_hash"]) == request_hash
                    and str(row["status"]) == status
                    and int(row["terminal_event_id"] or 0)
                    == int(terminal_event_id)
                ):
                    self._conn.commit()
                    return 0
                raise RuntimeError(
                    "historical operation terminal compare-and-set failed: "
                    f"{operation_id}"
                )
            except Exception:
                self._conn.rollback()
                raise

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
        worker_pgid: int | None = None,
        worker_started_at: float | None = None,
        worker_containment_id: str | None = None,
        returncode: int | None = None,
        error: str | None = None,
    ) -> None:
        now = int(time.time())
        recovery_point_value = recovery_point or _workflow_job_recovery_point(
            status=status,
            pid=pid,
        )
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT * FROM dual_agent_workflow_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                assert_terminal_workflow_job_mutation_allowed(
                    existing,
                    attempted={
                        "job_id": job_id,
                        "run_id": run_id,
                        "task_id": task_id,
                        "result_path": result_path,
                        "status": status,
                        "pid": pid,
                        "worker_pgid": worker_pgid,
                        "worker_started_at": worker_started_at,
                        "worker_containment_id": worker_containment_id,
                        "recovery_point": recovery_point_value,
                        "returncode": returncode,
                        "error": error,
                    },
                )
                self._conn.execute(
                    """INSERT INTO dual_agent_workflow_jobs(
                           job_id, run_id, task_id, cwd, status, pid,
                           worker_pgid, worker_started_at,
                           worker_containment_id,
                           request_path, result_path, log_path, idempotency_token,
                           recovery_point, request_payload_json, config_path,
                           returncode, error, created_at, updated_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(job_id) DO UPDATE SET
                           status=excluded.status,
                           pid=excluded.pid,
                           worker_pgid=excluded.worker_pgid,
                           worker_started_at=excluded.worker_started_at,
                           worker_containment_id=excluded.worker_containment_id,
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
                        worker_pgid,
                        worker_started_at,
                        worker_containment_id,
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
            except Exception:
                self._conn.rollback()
                raise

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
        worker_pgid: int | None = None,
        worker_started_at: float | None = None,
        worker_containment_id: str | None = None,
        worker_reaped_at: int | None = None,
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
        if worker_reaped_at is not None:
            raise RuntimeError(
                "worker_reaped_at may only be recorded through a "
                "containment-verified reap API"
            )
        assignments = ["updated_at=?"]
        params: list[Any] = [int(time.time())]
        if status is not None:
            assignments.append("status=?")
            params.append(status)
        if pid is not None:
            assignments.append("pid=?")
            params.append(pid)
        if worker_pgid is not None:
            assignments.append("worker_pgid=?")
            params.append(worker_pgid)
        if worker_started_at is not None:
            assignments.append("worker_started_at=?")
            params.append(worker_started_at)
        if worker_containment_id is not None:
            assignments.append("worker_containment_id=?")
            params.append(worker_containment_id)
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
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT * FROM dual_agent_workflow_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                attempted: dict[str, Any] = {}
                if status is not None:
                    attempted["status"] = status
                if pid is not None:
                    attempted["pid"] = pid
                if worker_pgid is not None:
                    attempted["worker_pgid"] = worker_pgid
                if worker_started_at is not None:
                    attempted["worker_started_at"] = worker_started_at
                if worker_containment_id is not None:
                    attempted["worker_containment_id"] = (
                        worker_containment_id
                    )
                if returncode is not None:
                    attempted["returncode"] = returncode
                if error is not None:
                    attempted["error"] = error
                if recovery_point is not None:
                    attempted["recovery_point"] = recovery_point
                assert_terminal_workflow_job_mutation_allowed(
                    existing,
                    attempted=attempted,
                )
                self._conn.execute(
                    f"""UPDATE dual_agent_workflow_jobs
                           SET {", ".join(assignments)}
                         WHERE job_id=?""",
                    params,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def count_active_dual_agent_workflow_job_leases(self, *, now: int) -> int:
        row = self._conn.execute(
            """SELECT COUNT(*) AS count
               FROM dual_agent_workflow_jobs
               WHERE recovery_point IN ('spawn_prepared', 'spawned')
                 AND status='running'
                 AND terminal_outcome_json IS NULL
                 AND leased_by IS NOT NULL
                 AND lease_expires_at IS NOT NULL
                 AND lease_expires_at > ?""",
            (now,),
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    def prepare_dual_agent_workflow_job_spawn(
        self,
        *,
        job_id: str,
        dispatcher_id: str,
        containment_id: str,
        lease_ttl_s: int,
        now: int,
    ) -> sqlite3.Row | None:
        """Persist cleanup ownership before crossing the subprocess seam."""
        now_value = int(now)
        lease_expires_at = now_value + max(1, int(lease_ttl_s))
        cleanup_owner = f"cleanup:{dispatcher_id}"
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status='running',
                              pid=NULL,
                              worker_pgid=NULL,
                              worker_started_at=NULL,
                              worker_prepared_at=?,
                              recovery_point='spawn_prepared',
                              worker_containment_id=?,
                              worker_reaped_at=NULL,
                              leased_by=?,
                              lease_expires_at=?,
                              heartbeat_at=?,
                              cleanup_attempts=0,
                              cleanup_escalated_at=NULL,
                              next_dispatch_at=NULL,
                              updated_at=?
                        WHERE job_id=?
                          AND recovery_point='request_written'
                          AND terminal_outcome_json IS NULL
                          AND (
                                pid IS NULL
                             OR worker_reaped_at IS NOT NULL
                          )
                          AND leased_by=?""",
                    (
                        float(now),
                        containment_id,
                        cleanup_owner,
                        lease_expires_at,
                        now_value,
                        now_value,
                        job_id,
                        dispatcher_id,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                self._conn.commit()
                return row
            except Exception:
                self._conn.rollback()
                raise

    def record_dual_agent_workflow_job_spawned(
        self,
        *,
        job_id: str,
        dispatcher_id: str,
        containment_id: str,
        pid: int,
        worker_pgid: int,
        worker_started_at: float | None,
        lease_ttl_s: int,
        now: int,
    ) -> sqlite3.Row | None:
        """Atomically bind one prepared containment to its worker identity."""
        now_value = int(now)
        lease_expires_at = now_value + max(1, int(lease_ttl_s))
        cleanup_owner = f"cleanup:{dispatcher_id}"
        worker_owner = f"worker:{int(pid)}"
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status='running',
                              pid=?,
                              worker_pgid=?,
                              worker_started_at=?,
                              recovery_point='spawned',
                              leased_by=?,
                              lease_expires_at=?,
                              heartbeat_at=?,
                              next_dispatch_at=NULL,
                              updated_at=?
                        WHERE job_id=?
                          AND recovery_point='spawn_prepared'
                          AND worker_containment_id=?
                          AND leased_by=?
                          AND terminal_outcome_json IS NULL
                          AND worker_reaped_at IS NULL""",
                    (
                        int(pid),
                        int(worker_pgid),
                        (
                            float(worker_started_at)
                            if worker_started_at is not None
                            else None
                        ),
                        worker_owner,
                        lease_expires_at,
                        now_value,
                        now_value,
                        job_id,
                        containment_id,
                        cleanup_owner,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                self._conn.commit()
                return row
            except Exception:
                self._conn.rollback()
                raise

    def release_dual_agent_workflow_job_spawn_preparation(
        self,
        *,
        job_id: str,
        containment_id: str,
        dispatch_attempts: int,
        error: str,
        next_dispatch_at: int | None = None,
        parked_reason: str | None = None,
    ) -> sqlite3.Row | None:
        """Release a prepared containment after Popen failed to create a worker."""
        now = int(time.time())
        status = "parked" if parked_reason is not None else "submitted"
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status=?,
                              pid=NULL,
                              worker_pgid=NULL,
                              worker_started_at=NULL,
                              worker_prepared_at=NULL,
                              worker_containment_id=NULL,
                              worker_reaped_at=NULL,
                              recovery_point='request_written',
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              heartbeat_at=NULL,
                              dispatch_attempts=?,
                              next_dispatch_at=?,
                              parked_reason=?,
                              error=?,
                              updated_at=?
                        WHERE job_id=?
                          AND recovery_point='spawn_prepared'
                          AND pid IS NULL
                          AND worker_reaped_at IS NULL
                          AND worker_containment_id=?
                          AND terminal_outcome_json IS NULL""",
                    (
                        status,
                        int(dispatch_attempts),
                        next_dispatch_at,
                        parked_reason,
                        error,
                        now,
                        job_id,
                        containment_id,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                self._conn.commit()
                return row
            except Exception:
                self._conn.rollback()
                raise

    def reschedule_dual_agent_workflow_job_after_reap(
        self,
        *,
        job_id: str,
        containment_id: str,
        dispatch_attempts: int,
        error: str,
        next_dispatch_at: int | None = None,
        parked_reason: str | None = None,
    ) -> sqlite3.Row | None:
        """Resubmit only after the prior worker's reap proof is durable."""
        now = int(time.time())
        status = "parked" if parked_reason is not None else "submitted"
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status=?,
                              recovery_point='request_written',
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              heartbeat_at=NULL,
                              dispatch_attempts=?,
                              next_dispatch_at=?,
                              parked_reason=?,
                              error=?,
                              updated_at=?
                        WHERE job_id=?
                          AND recovery_point IN (
                                'spawn_prepared', 'spawned'
                              )
                          AND worker_reaped_at IS NOT NULL
                          AND worker_containment_id=?
                          AND terminal_outcome_json IS NULL""",
                    (
                        status,
                        int(dispatch_attempts),
                        next_dispatch_at,
                        parked_reason,
                        error,
                        now,
                        job_id,
                        containment_id,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                self._conn.commit()
                return row
            except Exception:
                self._conn.rollback()
                raise

    def defer_dual_agent_workflow_job_cleanup(
        self,
        *,
        job_id: str,
        dispatcher_id: str,
        containment_id: str,
        reason: str,
        retry_delay_s: int,
        max_cleanup_retry_attempts: int,
        now: int,
    ) -> sqlite3.Row | None:
        """Durably retain cleanup ownership and escalate without parking."""
        now_value = int(now)
        lease_expires_at = now_value + max(1, int(retry_delay_s))
        cleanup_owner = f"cleanup:{dispatcher_id}"
        threshold = max(1, int(max_cleanup_retry_attempts))
        escalated_error = f"cleanup_retry_attempts_exhausted: {reason}"
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status='running',
                              error=CASE
                                WHEN cleanup_attempts + 1 > ?
                                THEN ?
                                ELSE ?
                              END,
                              leased_by=?,
                              lease_expires_at=?,
                              heartbeat_at=?,
                              cleanup_attempts=cleanup_attempts + 1,
                              cleanup_escalated_at=CASE
                                WHEN cleanup_attempts + 1 > ?
                                  THEN COALESCE(cleanup_escalated_at, ?)
                                ELSE cleanup_escalated_at
                              END,
                              updated_at=?
                        WHERE job_id=?
                          AND recovery_point IN (
                                'spawn_prepared', 'spawned'
                              )
                          AND terminal_outcome_json IS NULL
                          AND worker_reaped_at IS NULL
                          AND worker_containment_id=?""",
                    (
                        threshold,
                        escalated_error,
                        reason,
                        cleanup_owner,
                        lease_expires_at,
                        now_value,
                        threshold,
                        now_value,
                        now_value,
                        job_id,
                        containment_id,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                self._conn.commit()
                return row
            except Exception:
                self._conn.rollback()
                raise

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
                    "(pid IS NULL OR worker_reaped_at IS NOT NULL)",
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
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT * FROM dual_agent_workflow_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                assert_terminal_workflow_job_mutation_allowed(
                    existing,
                    attempted={} if error is None else {"error": error},
                )
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
            except Exception:
                self._conn.rollback()
                raise

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

    def claim_dual_agent_workflow_job_for_reap(
        self,
        *,
        job_id: str,
        reaper_id: str,
        lease_ttl_s: int,
        now: int,
        expected_leased_by: Any,
        expected_lease_expires_at: Any,
        expected_heartbeat_at: Any,
        expected_pid: Any,
        expected_worker_pgid: Any,
        expected_worker_started_at: Any,
        expected_worker_containment_id: Any,
    ) -> sqlite3.Row | None:
        """Fence signaling behind a full stale-snapshot compare-and-set."""
        now_value = int(now)
        lease_expires_at = now_value + max(1, int(lease_ttl_s))
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET leased_by=?,
                              lease_expires_at=?,
                              heartbeat_at=?,
                              updated_at=?
                        WHERE job_id=?
                          AND recovery_point IN (
                                'spawn_prepared', 'spawned'
                              )
                          AND status='running'
                          AND terminal_outcome_json IS NULL
                          AND worker_reaped_at IS NULL
                          AND leased_by IS ?
                          AND lease_expires_at IS ?
                          AND heartbeat_at IS ?
                          AND pid IS ?
                          AND worker_pgid IS ?
                          AND worker_started_at IS ?
                          AND worker_containment_id IS ?""",
                    (
                        reaper_id,
                        lease_expires_at,
                        now_value,
                        now_value,
                        job_id,
                        expected_leased_by,
                        expected_lease_expires_at,
                        expected_heartbeat_at,
                        expected_pid,
                        expected_worker_pgid,
                        expected_worker_started_at,
                        expected_worker_containment_id,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                self._conn.commit()
                return row
            except Exception:
                self._conn.rollback()
                raise

    def park_dual_agent_workflow_job(
        self,
        *,
        job_id: str,
        reason: str,
    ) -> sqlite3.Row | None:
        now = int(time.time())
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT * FROM dual_agent_workflow_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                assert_terminal_workflow_job_mutation_allowed(
                    existing,
                    attempted={"status": "parked", "error": reason},
                )
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
            except Exception:
                self._conn.rollback()
                raise

    def list_dual_agent_workflow_job_leases(self) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """SELECT *
               FROM dual_agent_workflow_jobs
               WHERE leased_by IS NOT NULL
                 AND terminal_outcome_json IS NULL
                 AND status!='parked'
               ORDER BY updated_at ASC, job_id ASC"""
        ))

    def list_terminal_dual_agent_workflow_jobs_pending_reap(
        self,
    ) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """SELECT *
                     FROM dual_agent_workflow_jobs
                    WHERE terminal_outcome_json IS NOT NULL
                      AND pid IS NOT NULL
                      AND worker_reaped_at IS NULL
                    ORDER BY updated_at ASC, job_id ASC"""
            )
        )

    def record_dual_agent_workflow_worker_reaped(
        self,
        *,
        job_id: str,
        worker_reaped_at: int,
        termination: dict[str, Any],
        observed_pid: int | None = None,
        observed_worker_pgid: int | None = None,
        observed_worker_started_at: float | None = None,
    ) -> int:
        """Atomically append reap evidence and freeze the one-way reap time."""
        reaped_at = int(worker_reaped_at)
        if (
            not isinstance(termination, dict)
            or termination.get("safe_to_finalize") is not True
        ):
            raise RuntimeError(
                "worker reap requires a successful containment termination proof"
            )
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """SELECT job_id, run_id, task_id, pid, worker_pgid,
                              worker_started_at, worker_containment_id,
                              worker_reaped_at, recovery_point
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"workflow job not found: {job_id}")
                if (
                    row["pid"] is None
                    and str(row["recovery_point"] or "")
                    != "spawn_prepared"
                ):
                    raise RuntimeError(
                        "cannot record a worker reap for an in-process job"
                    )
                if row["worker_reaped_at"] is not None:
                    if int(row["worker_reaped_at"]) != reaped_at:
                        raise RuntimeError(
                            "worker_reaped_at is immutable once recorded"
                        )
                    self._conn.commit()
                    return 0
                expected_containment = str(
                    row["worker_containment_id"] or ""
                )
                if (
                    not expected_containment
                    or str(termination.get("containment_id") or "")
                    != expected_containment
                ):
                    raise RuntimeError(
                        "worker reap containment identity mismatch"
                    )
                effective_pid = (
                    int(row["pid"])
                    if row["pid"] is not None
                    else (
                        int(observed_pid)
                        if observed_pid is not None
                        else None
                    )
                )
                effective_pgid = (
                    int(row["worker_pgid"])
                    if row["worker_pgid"] is not None
                    else (
                        int(observed_worker_pgid)
                        if observed_worker_pgid is not None
                        else None
                    )
                )
                effective_started_at = (
                    float(row["worker_started_at"])
                    if row["worker_started_at"] is not None
                    else (
                        float(observed_worker_started_at)
                        if observed_worker_started_at is not None
                        else None
                    )
                )
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET pid=COALESCE(pid, ?),
                              worker_pgid=COALESCE(worker_pgid, ?),
                              worker_started_at=COALESCE(
                                  worker_started_at, ?
                              ),
                              worker_reaped_at=?,
                              updated_at=?
                        WHERE job_id=?
                          AND worker_reaped_at IS NULL""",
                    (
                        effective_pid,
                        effective_pgid,
                        effective_started_at,
                        reaped_at,
                        int(time.time()),
                        job_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"worker reap compare-and-set failed: {job_id}"
                    )
                payload = self._event_payload(
                    run_id=row["run_id"],
                    source="dual_agent",
                    kind="dual_agent_workflow_worker_reaped",
                    payload={
                        "job_id": job_id,
                        "task_id": row["task_id"],
                        "pid": effective_pid,
                        "worker_pgid": effective_pgid,
                        "worker_started_at": effective_started_at,
                        "worker_containment_id": row[
                            "worker_containment_id"
                        ],
                        "worker_reaped_at": reaped_at,
                        "termination": termination,
                        "transport_recovery": "detached_cli_worker",
                    },
                )
                event_id = self._insert_event_unlocked(
                    run_id=row["run_id"],
                    source="dual_agent",
                    kind="dual_agent_workflow_worker_reaped",
                    payload=payload,
                    ts=reaped_at,
                )
                self._conn.commit()
                self._coordinate_committed_event(
                    run_id=row["run_id"],
                    event_id=event_id,
                    event_kind="dual_agent_workflow_worker_reaped",
                )
                return event_id
            except Exception:
                self._conn.rollback()
                raise

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
        worker_reaped_at: int | None = None,
        termination: dict[str, Any] | None = None,
    ) -> int:
        """Atomically persist a detached workflow job's terminal status/outcome."""
        if not isinstance(terminal_outcome, dict) or not terminal_outcome:
            raise ValueError("terminal_outcome must be a non-empty dict")
        now = int(time.time())
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """SELECT job_id, run_id, task_id, result_path, status,
                              pid, worker_pgid, worker_started_at,
                              worker_containment_id, worker_reaped_at,
                              recovery_point, terminal_status,
                              terminal_outcome_json, terminal_outcome_recorded_at,
                              returncode, error
                       FROM dual_agent_workflow_jobs
                       WHERE job_id=?""",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"workflow job not found: {job_id}")
                existing_outcome_json = row["terminal_outcome_json"]
                persisted_reaped_at = row["worker_reaped_at"]
                reaped_at_value = (
                    int(persisted_reaped_at)
                    if persisted_reaped_at is not None
                    else (
                        int(worker_reaped_at)
                        if worker_reaped_at is not None
                        else None
                    )
                )
                if (
                    persisted_reaped_at is not None
                    and worker_reaped_at is not None
                    and int(persisted_reaped_at) != int(worker_reaped_at)
                ):
                    raise RuntimeError(
                        "worker_reaped_at is immutable once recorded"
                    )
                if row["pid"] is not None and existing_outcome_json is None:
                    if reaped_at_value is None:
                        raise RuntimeError(
                            "worker reap must be recorded atomically before "
                            "terminal publication"
                        )
                    if persisted_reaped_at is None:
                        if (
                            not isinstance(termination, dict)
                            or termination.get("safe_to_finalize") is not True
                        ):
                            raise RuntimeError(
                                "worker reap requires a successful containment "
                                "termination proof"
                            )
                        expected_containment = str(
                            row["worker_containment_id"] or ""
                        )
                        observed_containment = str(
                            termination.get("containment_id") or ""
                        )
                        if (
                            not expected_containment
                            or observed_containment != expected_containment
                        ):
                            raise RuntimeError(
                                "worker reap containment identity mismatch"
                            )
                elif (
                    row["pid"] is None
                    and worker_reaped_at is not None
                ):
                    raise RuntimeError(
                        "cannot record a worker reap for an in-process job"
                    )
                validation_error: str | None = None
                try:
                    terminal_status_value = validate_terminal_completion(
                        job_id=job_id,
                        run_id=row["run_id"],
                        task_id=row["task_id"],
                        status=status,
                        terminal_status=terminal_status,
                        terminal_outcome=terminal_outcome,
                    )
                except ValueError as exc:
                    if existing_outcome_json is None:
                        raise
                    validation_error = str(exc)
                    terminal_status_value = str(
                        terminal_status
                        or terminal_outcome.get("status")
                        or status
                    ).strip()
                outcome_json = canonical_terminal_outcome_json(terminal_outcome)
                if existing_outcome_json is not None:
                    existing_semantics = canonical_terminal_completion_semantics(
                        job_id=row["job_id"],
                        run_id=row["run_id"],
                        task_id=row["task_id"],
                        result_path=row["result_path"],
                        status=row["status"],
                        terminal_status=row["terminal_status"],
                        terminal_outcome_json=str(existing_outcome_json),
                        returncode=row["returncode"],
                        error=row["error"],
                    )
                    incoming_semantics = canonical_terminal_completion_semantics(
                        job_id=job_id,
                        run_id=row["run_id"],
                        task_id=row["task_id"],
                        result_path=row["result_path"],
                        status=status,
                        terminal_status=terminal_status_value,
                        terminal_outcome_json=outcome_json,
                        returncode=returncode,
                        error=error,
                    )
                    if (
                        validation_error is None
                        and existing_semantics == incoming_semantics
                    ):
                        terminal_event = self._conn.execute(
                            """SELECT event_id
                                 FROM events
                                WHERE run_id=?
                                  AND kind='dual_agent_workflow_terminal_outcome'
                                  AND json_extract(payload_json, '$.job_id')=?
                                ORDER BY event_id DESC
                                LIMIT 1""",
                            (row["run_id"], job_id),
                        ).fetchone()
                        self._conn.commit()
                        if terminal_event is not None:
                            self._coordinate_committed_event(
                                run_id=row["run_id"],
                                event_id=int(terminal_event["event_id"]),
                                event_kind=(
                                    "dual_agent_workflow_terminal_outcome"
                                ),
                            )
                        return 0
                    conflict_sha256 = terminal_completion_conflict_sha256(
                        original=existing_semantics,
                        conflicting=incoming_semantics,
                        validation_error=validation_error,
                    )
                    original_record = canonical_terminal_completion_record(
                        job_id=row["job_id"],
                        run_id=row["run_id"],
                        task_id=row["task_id"],
                        result_path=row["result_path"],
                        status=row["status"],
                        recovery_point=row["recovery_point"],
                        terminal_status=row["terminal_status"],
                        terminal_outcome_json=str(existing_outcome_json),
                        terminal_outcome_recorded_at=row[
                            "terminal_outcome_recorded_at"
                        ],
                        returncode=row["returncode"],
                        error=row["error"],
                    )
                    conflicting_record = canonical_terminal_completion_record(
                        job_id=job_id,
                        run_id=row["run_id"],
                        task_id=row["task_id"],
                        result_path=row["result_path"],
                        status=status,
                        recovery_point="terminal",
                        terminal_status=terminal_status_value,
                        terminal_outcome_json=outcome_json,
                        terminal_outcome_recorded_at=now,
                        returncode=returncode,
                        error=error,
                    )
                    discrepancy_payload = self._event_payload(
                        run_id=row["run_id"],
                        source="dual_agent",
                        kind="dual_agent_workflow_terminal_discrepancy",
                        payload={
                            "job_id": job_id,
                            "task_id": row["task_id"],
                            "result_path": row["result_path"],
                            "original_status": row["status"],
                            "original_terminal_status": row["terminal_status"],
                            "original_terminal_outcome": json.loads(str(existing_outcome_json)),
                            "original_returncode": row["returncode"],
                            "original_error": row["error"],
                            "conflicting_status": status,
                            "conflicting_terminal_status": terminal_status_value,
                            "conflicting_terminal_outcome": json.loads(outcome_json),
                            "conflicting_returncode": returncode,
                            "conflicting_error": error,
                            "conflicting_validation_error": validation_error,
                            "conflict_sha256": conflict_sha256,
                            "original_terminal_record": original_record,
                            "original_terminal_record_sha256": (
                                terminal_completion_record_sha256(original_record)
                            ),
                            "conflicting_terminal_record": conflicting_record,
                            "conflicting_terminal_record_sha256": (
                                terminal_completion_record_sha256(
                                    conflicting_record
                                )
                            ),
                            "transport_recovery": "detached_cli_worker",
                        },
                    )
                    existing_discrepancy = self._conn.execute(
                        """SELECT event_id
                             FROM events
                            WHERE run_id=?
                              AND kind='dual_agent_workflow_terminal_discrepancy'
                              AND json_extract(
                                    payload_json,
                                    '$.conflict_sha256'
                                  )=?
                            LIMIT 1""",
                        (row["run_id"], conflict_sha256),
                    ).fetchone()
                    if existing_discrepancy is None:
                        discrepancy_event_id = self._insert_event_unlocked(
                            run_id=row["run_id"],
                            source="dual_agent",
                            kind="dual_agent_workflow_terminal_discrepancy",
                            payload=discrepancy_payload,
                        )
                    else:
                        discrepancy_event_id = int(
                            existing_discrepancy["event_id"]
                        )
                    self._conn.commit()
                    self._coordinate_committed_event(
                        run_id=row["run_id"],
                        event_id=discrepancy_event_id,
                        event_kind=(
                            "dual_agent_workflow_terminal_discrepancy"
                        ),
                    )
                    raise RuntimeError(
                        f"workflow job terminal outcome discrepancy: {job_id}"
                    )
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET status=?,
                              recovery_point='terminal',
                              recovery_claim_token=NULL,
                              recovery_claimed_at=NULL,
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              heartbeat_at=NULL,
                              worker_reaped_at=?,
                              terminal_status=?,
                              terminal_outcome_json=?,
                              terminal_outcome_recorded_at=?,
                              returncode=?,
                              error=?,
                              updated_at=?
                        WHERE job_id=?
                          AND terminal_outcome_json IS NULL""",
                    (
                        status,
                        reaped_at_value,
                        terminal_status_value,
                        outcome_json,
                        now,
                        returncode,
                        error,
                        now,
                        job_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"workflow job terminal completion compare-and-set failed: {job_id}"
                    )
                terminal_record = canonical_terminal_completion_record(
                    job_id=job_id,
                    run_id=row["run_id"],
                    task_id=row["task_id"],
                    result_path=row["result_path"],
                    status=status,
                    recovery_point="terminal",
                    terminal_status=terminal_status_value,
                    terminal_outcome_json=outcome_json,
                    terminal_outcome_recorded_at=now,
                    returncode=returncode,
                    error=error,
                )
                if (
                    row["pid"] is not None
                    and persisted_reaped_at is None
                    and reaped_at_value is not None
                ):
                    reap_payload = self._event_payload(
                        run_id=row["run_id"],
                        source="dual_agent",
                        kind="dual_agent_workflow_worker_reaped",
                        payload={
                            "job_id": job_id,
                            "task_id": row["task_id"],
                            "pid": row["pid"],
                            "worker_pgid": row["worker_pgid"],
                            "worker_started_at": row["worker_started_at"],
                            "worker_containment_id": row[
                                "worker_containment_id"
                            ],
                            "worker_reaped_at": reaped_at_value,
                            "termination": termination,
                            "transport_recovery": "detached_cli_worker",
                        },
                    )
                    self._insert_event_unlocked(
                        run_id=row["run_id"],
                        source="dual_agent",
                        kind="dual_agent_workflow_worker_reaped",
                        payload=reap_payload,
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
                        "terminal_record": terminal_record,
                        "terminal_record_sha256": (
                            terminal_completion_record_sha256(terminal_record)
                        ),
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
                self._coordinate_committed_event(
                    run_id=row["run_id"],
                    event_id=event_id,
                    event_kind="dual_agent_workflow_terminal_outcome",
                )
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

    def commit_decision_verdict(
        self,
        decision: Decision,
        *,
        model: str,
        output: dict[str, Any],
        latency_ms: int,
        mode: str | None = None,
        event_id: int | None = None,
        now: float | None = None,
    ) -> int | None:
        """Atomically publish one leased decision verdict and acknowledge it.

        Decision identity and verdict routing are loaded from the durable
        outbox row. A stale, expired, released, or caller-substituted lease
        cannot publish a verdict.
        """

        safe = redact(output)
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                timestamp = time.time() if now is None else float(now)
                row = self._conn.execute(
                    """SELECT decision_id, kind, run_id, status, lease_token,
                              lease_expires_at
                         FROM decision_outbox
                        WHERE decision_id=?""",
                    (str(decision.decision_id),),
                ).fetchone()
                if (
                    row is None
                    or str(row["kind"]) != str(decision.kind)
                    or str(row["run_id"]) != str(decision.run_id)
                    or str(row["status"]) != "leased"
                    or str(row["lease_token"] or "")
                    != str(decision.lease_token)
                    or row["lease_expires_at"] is None
                    or float(row["lease_expires_at"]) <= timestamp
                ):
                    self._conn.rollback()
                    return None

                kind = str(row["kind"])
                run_id = str(row["run_id"])
                layer = "L4" if kind == "adjudicate_drift" else None
                cur = self._conn.execute(
                    """INSERT INTO verdicts(
                         decision_id, run_id, event_id, phase, layer, model,
                         output_json, latency_ms, mode, created_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(row["decision_id"]),
                        run_id,
                        event_id,
                        kind,
                        layer,
                        model,
                        json.dumps(safe),
                        latency_ms,
                        mode,
                        int(timestamp),
                    ),
                )
                changed = self._conn.execute(
                    """UPDATE decision_outbox
                          SET status='acked',
                              lease_token=NULL,
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              updated_at=?,
                              acked_at=?
                        WHERE decision_id=?
                          AND kind=?
                          AND run_id=?
                          AND status='leased'
                          AND lease_token=?
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at > ?""",
                    (
                        timestamp,
                        timestamp,
                        str(row["decision_id"]),
                        kind,
                        run_id,
                        str(decision.lease_token),
                        timestamp,
                    ),
                ).rowcount
                if changed != 1:
                    self._conn.rollback()
                    return None
                verdict_id = int(cur.lastrowid or 0)
                self._conn.commit()
                return verdict_id
            except Exception:
                self._conn.rollback()
                raise

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

    # --- durable decision outbox ---
    def _decision_idempotency_key(self, decision: Decision) -> str:
        canonical = json.dumps(
            {
                "kind": str(decision.kind),
                "run_id": str(decision.run_id),
                "payload": decision.payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
        return hashlib.sha256(
            f"supervisor-decision/v1\0{canonical}".encode("utf-8")
        ).hexdigest()

    def _enqueue_decision_unlocked(
        self,
        decision: Decision,
        *,
        idempotency_key: str | None = None,
        available_at: float | None = None,
    ) -> str:
        key = str(
            idempotency_key or self._decision_idempotency_key(decision)
        )
        now = time.time()
        decision_id = str(decision.decision_id or uuid.uuid4().hex)
        self._conn.execute(
            """INSERT INTO decision_outbox(
                 decision_id, idempotency_key, kind, run_id, payload_json,
                 status, attempts, available_at, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
               ON CONFLICT(idempotency_key) DO NOTHING""",
            (
                decision_id,
                key,
                str(decision.kind),
                str(decision.run_id),
                json.dumps(
                    redact(decision.payload),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    default=str,
                ),
                now if available_at is None else float(available_at),
                now,
                now,
            ),
        )
        row = self._conn.execute(
            """SELECT decision_id
                 FROM decision_outbox
                WHERE idempotency_key=?""",
            (key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("decision outbox insert disappeared")
        return str(row["decision_id"])

    async def enqueue_decision(
        self,
        d: Decision,
        *,
        idempotency_key: str | None = None,
        available_at: float | None = None,
    ) -> str:
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                decision_id = self._enqueue_decision_unlocked(
                    d,
                    idempotency_key=idempotency_key,
                    available_at=available_at,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        self._decision_wakeup.set()
        return decision_id

    def claim_decision(
        self,
        *,
        worker_id: str,
        lease_s: float = 60.0,
        now: float | None = None,
    ) -> Decision | None:
        lease_seconds = max(0.001, float(lease_s))
        lease_token = uuid.uuid4().hex
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                timestamp = time.time() if now is None else float(now)
                row = self._conn.execute(
                    """SELECT decision_id
                         FROM decision_outbox
                        WHERE (
                              status='pending'
                              AND available_at <= ?
                            )
                           OR (
                              status='leased'
                              AND lease_expires_at IS NOT NULL
                              AND lease_expires_at <= ?
                            )
                        ORDER BY available_at ASC, created_at ASC, decision_id ASC
                        LIMIT 1""",
                    (timestamp, timestamp),
                ).fetchone()
                if row is None:
                    self._conn.commit()
                    return None
                updated = self._conn.execute(
                    """UPDATE decision_outbox
                          SET status='leased',
                              attempts=attempts + 1,
                              lease_token=?,
                              leased_by=?,
                              lease_expires_at=?,
                              updated_at=?
                        WHERE decision_id=?
                          AND (
                                (status='pending' AND available_at <= ?)
                             OR (
                                status='leased'
                                AND lease_expires_at IS NOT NULL
                                AND lease_expires_at <= ?
                             )
                          )""",
                    (
                        lease_token,
                        str(worker_id),
                        timestamp + lease_seconds,
                        timestamp,
                        str(row["decision_id"]),
                        timestamp,
                        timestamp,
                    ),
                ).rowcount
                if updated != 1:
                    self._conn.rollback()
                    return None
                claimed = self._conn.execute(
                    """SELECT decision_id, kind, run_id, payload_json,
                              lease_token, attempts
                         FROM decision_outbox
                        WHERE decision_id=?""",
                    (str(row["decision_id"]),),
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if claimed is None:
            return None
        return Decision(
            kind=str(claimed["kind"]),
            run_id=str(claimed["run_id"]),
            payload=json.loads(str(claimed["payload_json"])),
            decision_id=str(claimed["decision_id"]),
            lease_token=str(claimed["lease_token"]),
            attempt_count=int(claimed["attempts"]),
        )

    async def next_decision(
        self,
        *,
        lease_s: float = 60.0,
    ) -> Decision:
        while True:
            decision = self.claim_decision(
                worker_id=self._decision_worker_id,
                lease_s=lease_s,
            )
            if decision is not None:
                return decision
            self._decision_wakeup.clear()
            try:
                await asyncio.wait_for(
                    self._decision_wakeup.wait(),
                    timeout=0.25,
                )
            except asyncio.TimeoutError:
                pass

    def ack_decision(
        self,
        decision: Decision,
        *,
        now: float | None = None,
    ) -> bool:
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                timestamp = time.time() if now is None else float(now)
                changed = self._conn.execute(
                    """UPDATE decision_outbox
                          SET status='acked',
                              lease_token=NULL,
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              updated_at=?,
                              acked_at=?
                        WHERE decision_id=?
                          AND kind=?
                          AND run_id=?
                          AND status='leased'
                          AND lease_token=?
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at > ?""",
                    (
                        timestamp,
                        timestamp,
                        str(decision.decision_id),
                        str(decision.kind),
                        str(decision.run_id),
                        str(decision.lease_token),
                        timestamp,
                    ),
                ).rowcount
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return changed == 1

    def retry_decision(
        self,
        decision: Decision,
        *,
        error: str,
        delay_s: float,
        now: float | None = None,
    ) -> bool:
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                timestamp = time.time() if now is None else float(now)
                available_at = timestamp + max(0.0, float(delay_s))
                changed = self._conn.execute(
                    """UPDATE decision_outbox
                          SET status='pending',
                              available_at=?,
                              lease_token=NULL,
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              last_error=?,
                              updated_at=?
                        WHERE decision_id=?
                          AND kind=?
                          AND run_id=?
                          AND status='leased'
                          AND lease_token=?
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at > ?""",
                    (
                        available_at,
                        str(error)[:4000],
                        timestamp,
                        str(decision.decision_id),
                        str(decision.kind),
                        str(decision.run_id),
                        str(decision.lease_token),
                        timestamp,
                    ),
                ).rowcount
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if changed:
            self._decision_wakeup.set()
        return changed == 1

    def dead_letter_decision(
        self,
        decision: Decision,
        *,
        error: str,
        now: float | None = None,
    ) -> bool:
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                timestamp = time.time() if now is None else float(now)
                changed = self._conn.execute(
                    """UPDATE decision_outbox
                          SET status='dead_letter',
                              lease_token=NULL,
                              leased_by=NULL,
                              lease_expires_at=NULL,
                              last_error=?,
                              updated_at=?,
                              dead_lettered_at=?
                        WHERE decision_id=?
                          AND kind=?
                          AND run_id=?
                          AND status='leased'
                          AND lease_token=?
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at > ?""",
                    (
                        str(error)[:4000],
                        timestamp,
                        timestamp,
                        str(decision.decision_id),
                        str(decision.kind),
                        str(decision.run_id),
                        str(decision.lease_token),
                        timestamp,
                    ),
                ).rowcount
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return changed == 1

    def available_decision_count(
        self,
        *,
        now: float | None = None,
    ) -> int:
        timestamp = time.time() if now is None else float(now)
        row = self._conn.execute(
            """SELECT COUNT(*) AS count
                 FROM decision_outbox
                WHERE (status='pending' AND available_at <= ?)
                   OR (
                      status='leased'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at <= ?
                   )""",
            (timestamp, timestamp),
        ).fetchone()
        return int(row["count"]) if row is not None else 0

    def list_decision_outbox(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status is None:
            rows = self._conn.execute(
                """SELECT * FROM decision_outbox
                   ORDER BY created_at ASC, decision_id ASC"""
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM decision_outbox
                    WHERE status=?
                   ORDER BY created_at ASC, decision_id ASC""",
                (str(status),),
            ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in rows
        ]
