"""Postgres-backed event/job lane for multi-writer supervisor deployments."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import TYPE_CHECKING, Any, Mapping

from .evidence_ledger import (
    EVIDENCE_COMMIT_EVENT_KIND,
    EVIDENCE_COMMIT_EVENT_SOURCE,
    NATIVE_GENESIS,
    LedgerVerification,
    build_ledger_fields,
    canonical_payload_hash,
    prepare_event_payload,
    verify_event_chain,
    verify_event_chain_structure,
)
from .redaction import redact
from .quality_projection import (
    QUALITY_TREND_PROJECTION_EVENT,
    assert_generic_event_kind_allowed,
    canonical_quality_trend_projection_row,
    quality_trend_projection_event_payload,
    rebuild_quality_trend_projection,
)
from .state import (
    HISTORICAL_OPERATION_EVENT_SOURCE,
    TERMINAL_WORKFLOW_JOB_STATUSES,
    _assert_evidence_committer_owner,
    assert_historical_operation_event_source,
    assert_public_event_kind_allowed,
    assert_terminal_workflow_job_mutation_allowed,
    canonical_terminal_completion_record,
    canonical_terminal_completion_semantics,
    canonical_terminal_outcome_json,
    terminal_completion_conflict_sha256,
    terminal_completion_record_sha256,
    validate_terminal_completion,
    validate_quality_audit_counts,
)
from .lessons import canonical_lesson_id, canonical_lesson_key

if TYPE_CHECKING:
    from .ledger_checkpoints import LedgerCheckpointCoordinator


POSTGRES_LOCK_ORDER = "priority ASC, created_at ASC, id ASC"
POSTGRES_ALEMBIC_HEAD = "20260712_0004"

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

POSTGRES_CLAIM_WORKFLOW_JOB_FOR_REAP_SQL = """
UPDATE dual_agent_workflow_jobs AS j
   SET leased_by = %(reaper_id)s,
       lease_expires_at = %(claim_expires_at)s,
       heartbeat_at = %(now)s,
       updated_at = %(now)s
 WHERE j.job_id = %(job_id)s
   AND j.recovery_point = 'spawned'
   AND j.status = 'running'
   AND j.terminal_outcome_json IS NULL
   AND j.worker_reaped_at IS NULL
   AND j.leased_by IS NOT DISTINCT FROM %(expected_leased_by)s
   AND j.lease_expires_at
       IS NOT DISTINCT FROM %(expected_lease_expires_at)s
   AND j.heartbeat_at IS NOT DISTINCT FROM %(expected_heartbeat_at)s
   AND j.pid IS NOT DISTINCT FROM %(expected_pid)s
   AND j.worker_pgid IS NOT DISTINCT FROM %(expected_worker_pgid)s
   AND j.worker_started_at
       IS NOT DISTINCT FROM %(expected_worker_started_at)s
   AND j.worker_containment_id
       IS NOT DISTINCT FROM %(expected_worker_containment_id)s
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
  event_sequence BIGINT NOT NULL,
  previous_event_id BIGINT,
  ts BIGINT NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  previous_event_hash TEXT,
  event_hash TEXT NOT NULL,
  canonical_payload_hash TEXT NOT NULL,
  artifact_manifest_hash TEXT NOT NULL,
  ledger_genesis_kind TEXT,
  CONSTRAINT events_run_event_unique UNIQUE(run_id, event_id),
  CONSTRAINT events_run_sequence_unique UNIQUE(run_id, event_sequence),
  CONSTRAINT events_sequence_positive CHECK (event_sequence > 0),
  CONSTRAINT events_previous_id_shape CHECK (
       (event_id = 1 AND previous_event_id IS NULL)
    OR (event_id > 1 AND previous_event_id = event_id - 1)
  ),
  CONSTRAINT events_genesis_hash_shape CHECK (
       (previous_event_hash IS NULL AND ledger_genesis_kind IN ('native', 'legacy-import'))
    OR (previous_event_hash IS NOT NULL AND ledger_genesis_kind IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_events_run_event ON events(run_id, event_id);
CREATE INDEX IF NOT EXISTS idx_events_run_ts ON events(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_global_id ON events(global_id);

CREATE TABLE IF NOT EXISTS event_idempotency_claims (
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  event_id BIGINT,
  source TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at BIGINT NOT NULL,
  PRIMARY KEY(run_id, kind, idempotency_key),
  UNIQUE(run_id, event_id)
);
CREATE OR REPLACE FUNCTION enforce_event_idempotency_claim_immutability()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF OLD.event_id IS NULL AND NEW.event_id IS NOT NULL
       AND NEW.run_id IS NOT DISTINCT FROM OLD.run_id
       AND NEW.kind IS NOT DISTINCT FROM OLD.kind
       AND NEW.idempotency_key IS NOT DISTINCT FROM OLD.idempotency_key
       AND NEW.source IS NOT DISTINCT FROM OLD.source
       AND NEW.payload_sha256 IS NOT DISTINCT FROM OLD.payload_sha256
       AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
       AND EXISTS (
         SELECT 1
         FROM events AS event
         WHERE event.run_id = NEW.run_id
           AND event.event_id = NEW.event_id
           AND event.kind = NEW.kind
           AND event.source = NEW.source
           AND event.canonical_payload_hash = NEW.payload_sha256
       ) THEN
      RETURN NEW;
    END IF;
  END IF;
  RAISE EXCEPTION 'event idempotency claims are immutable';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS event_idempotency_claims_immutable
  ON event_idempotency_claims;
CREATE TRIGGER event_idempotency_claims_immutable
BEFORE UPDATE OR DELETE ON event_idempotency_claims
FOR EACH ROW EXECUTE FUNCTION enforce_event_idempotency_claim_immutability();
DROP TRIGGER IF EXISTS event_idempotency_claims_no_truncate
  ON event_idempotency_claims;
CREATE TRIGGER event_idempotency_claims_no_truncate
BEFORE TRUNCATE ON event_idempotency_claims
FOR EACH STATEMENT EXECUTE FUNCTION
  enforce_event_idempotency_claim_immutability();

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

CREATE TABLE IF NOT EXISTS historical_operation_claims (
  operation_id TEXT PRIMARY KEY,
  request_hash TEXT NOT NULL,
  operation TEXT NOT NULL
    CHECK(operation IN ('rerun', 'regrade', 'replay')),
  status TEXT NOT NULL
    CHECK(status IN ('running', 'completed', 'failed')),
  terminal_event_id BIGINT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_historical_operation_claims_status
  ON historical_operation_claims(status, updated_at);

CREATE TABLE IF NOT EXISTS dual_agent_workflow_jobs (
  id BIGSERIAL UNIQUE,
  job_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  cwd TEXT NOT NULL,
  status TEXT NOT NULL,
  pid INTEGER,
  worker_pgid INTEGER,
  worker_started_at DOUBLE PRECISION,
  worker_containment_id TEXT,
  worker_reaped_at BIGINT,
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

CREATE TABLE IF NOT EXISTS supervisor_lessons (
  lesson_id TEXT PRIMARY KEY,
  task_class TEXT NOT NULL,
  gate TEXT NOT NULL,
  taxonomy_code TEXT NOT NULL,
  root_cause TEXT NOT NULL,
  remediation TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  normalized_key TEXT NOT NULL DEFAULT '',
  observed_count INTEGER NOT NULL DEFAULT 1,
  injection_count INTEGER NOT NULL DEFAULT 0,
  recurrence_count INTEGER NOT NULL DEFAULT 0,
  retired_at BIGINT,
  created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_supervisor_lessons_task_gate
  ON supervisor_lessons(task_class, gate, created_at);

CREATE TABLE IF NOT EXISTS supervisor_quality_trends (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  gate TEXT NOT NULL,
  accepted BOOLEAN NOT NULL,
  first_pass_accepted BOOLEAN NOT NULL,
  revision_rounds INTEGER NOT NULL,
  time_to_accepted_outcome_s DOUBLE PRECISION,
  p11_audit_sample_size INTEGER NOT NULL DEFAULT 0,
  false_accept_count INTEGER NOT NULL DEFAULT 0,
  false_accept_denominator INTEGER NOT NULL DEFAULT 0,
  false_accept_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  policy_overlay_hash TEXT NOT NULL DEFAULT '',
  policy_proposal_id TEXT NOT NULL DEFAULT '',
  details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  computed_at BIGINT NOT NULL,
  UNIQUE(run_id, gate)
);
CREATE INDEX IF NOT EXISTS idx_supervisor_quality_trends_task_gate
  ON supervisor_quality_trends(task_class, gate, computed_at);

CREATE TABLE IF NOT EXISTS quality_trend_audits (
  run_id TEXT NOT NULL,
  gate TEXT NOT NULL,
  sample_size INTEGER NOT NULL,
  false_accept_count INTEGER NOT NULL,
  false_accept_denominator INTEGER NOT NULL,
  false_accept_rate DOUBLE PRECISION NOT NULL,
  audit_details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  computed_at BIGINT NOT NULL,
  PRIMARY KEY(run_id, gate, computed_at),
  CONSTRAINT quality_trend_audits_counts_valid CHECK (
       sample_size >= 0
   AND false_accept_count >= 0
   AND false_accept_denominator >= 0
   AND false_accept_count <= false_accept_denominator
   AND false_accept_denominator <= sample_size
   AND false_accept_rate >= 0.0
   AND false_accept_rate <= 1.0
  ),
  CONSTRAINT quality_trend_audits_rate_exact CHECK (
    false_accept_rate = CASE
      WHEN false_accept_denominator = 0 THEN 0.0
      ELSE false_accept_count::double precision
           / false_accept_denominator::double precision
    END
  )
);
CREATE INDEX IF NOT EXISTS idx_quality_trend_audits_run_gate
  ON quality_trend_audits(run_id, gate, computed_at);

CREATE TABLE IF NOT EXISTS supervisor_autoresearch_experiments (
  experiment_id TEXT PRIMARY KEY,
  signal_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  task_class TEXT NOT NULL,
  gate TEXT NOT NULL,
  taxonomy_code TEXT NOT NULL,
  experiment_json JSONB NOT NULL,
  attempt_json JSONB NOT NULL,
  provenance_json JSONB NOT NULL,
  report_only_reason TEXT NOT NULL DEFAULT '',
  proposal_pointer_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  report_ref TEXT NOT NULL DEFAULT '',
  report_sha256 TEXT NOT NULL DEFAULT '',
  last_run_id TEXT NOT NULL DEFAULT '',
  last_run_started_at BIGINT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  activated_at BIGINT,
  activated_by TEXT,
  activation_channel TEXT
);
CREATE INDEX IF NOT EXISTS idx_supervisor_autoresearch_experiments_status
  ON supervisor_autoresearch_experiments(status, updated_at);

ALTER TABLE events ADD COLUMN IF NOT EXISTS previous_event_hash TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS event_hash TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS canonical_payload_hash TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS artifact_manifest_hash TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ledger_genesis_kind TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS event_sequence BIGINT;
ALTER TABLE dual_agent_workflow_jobs
  ADD COLUMN IF NOT EXISTS worker_pgid INTEGER;
ALTER TABLE dual_agent_workflow_jobs
  ADD COLUMN IF NOT EXISTS worker_started_at DOUBLE PRECISION;
ALTER TABLE dual_agent_workflow_jobs
  ADD COLUMN IF NOT EXISTS worker_containment_id TEXT;
ALTER TABLE dual_agent_workflow_jobs
  ADD COLUMN IF NOT EXISTS worker_reaped_at BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_hash ON events(event_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_run_sequence
  ON events(run_id, event_sequence);
"""


POSTGRES_EVENT_IMMUTABILITY_SQL = """
ALTER TABLE events ALTER COLUMN event_sequence SET NOT NULL;
ALTER TABLE events ALTER COLUMN event_hash SET NOT NULL;
ALTER TABLE events ALTER COLUMN canonical_payload_hash SET NOT NULL;
ALTER TABLE events ALTER COLUMN artifact_manifest_hash SET NOT NULL;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM pg_constraint
     WHERE conname = 'events_run_sequence_unique'
       AND conrelid = 'events'::regclass
  ) THEN
    ALTER TABLE events
      ADD CONSTRAINT events_run_sequence_unique
      UNIQUE(run_id, event_sequence);
  END IF;
  IF NOT EXISTS (
    SELECT 1
      FROM pg_constraint
     WHERE conname = 'events_sequence_positive'
       AND conrelid = 'events'::regclass
  ) THEN
    ALTER TABLE events
      ADD CONSTRAINT events_sequence_positive
      CHECK(event_sequence > 0);
  END IF;
  IF NOT EXISTS (
    SELECT 1
      FROM pg_constraint
     WHERE conname = 'quality_trend_audits_counts_valid'
       AND conrelid = 'quality_trend_audits'::regclass
  ) THEN
    ALTER TABLE quality_trend_audits
      ADD CONSTRAINT quality_trend_audits_counts_valid CHECK (
           sample_size >= 0
       AND false_accept_count >= 0
       AND false_accept_denominator >= 0
       AND false_accept_count <= false_accept_denominator
       AND false_accept_denominator <= sample_size
       AND false_accept_rate >= 0.0
       AND false_accept_rate <= 1.0
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
      FROM pg_constraint
     WHERE conname = 'quality_trend_audits_rate_exact'
       AND conrelid = 'quality_trend_audits'::regclass
  ) THEN
    ALTER TABLE quality_trend_audits
      ADD CONSTRAINT quality_trend_audits_rate_exact CHECK (
        false_accept_rate = CASE
          WHEN false_accept_denominator = 0 THEN 0.0
          ELSE false_accept_count::double precision
               / false_accept_denominator::double precision
        END
      );
  END IF;
END;
$$;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM pg_constraint
     WHERE conname = 'events_genesis_hash_shape'
       AND conrelid = 'events'::regclass
  ) THEN
    ALTER TABLE events
      ADD CONSTRAINT events_genesis_hash_shape CHECK (
           (previous_event_hash IS NULL AND ledger_genesis_kind IN ('native', 'legacy-import'))
        OR (previous_event_hash IS NOT NULL AND ledger_genesis_kind IS NULL)
      );
  END IF;
END;
$$;
CREATE OR REPLACE FUNCTION reject_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'events are append-only';
END;
$$;
DROP TRIGGER IF EXISTS events_no_update ON events;
CREATE TRIGGER events_no_update
BEFORE UPDATE ON events
FOR EACH ROW EXECUTE FUNCTION reject_event_mutation();
DROP TRIGGER IF EXISTS events_no_delete ON events;
CREATE TRIGGER events_no_delete
BEFORE DELETE ON events
FOR EACH ROW EXECUTE FUNCTION reject_event_mutation();
DROP TRIGGER IF EXISTS events_no_truncate ON events;
CREATE TRIGGER events_no_truncate
BEFORE TRUNCATE ON events
FOR EACH STATEMENT EXECUTE FUNCTION reject_event_mutation();
CREATE OR REPLACE FUNCTION reject_quality_trend_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'quality trend audits are immutable';
END;
$$;
DROP TRIGGER IF EXISTS quality_trend_audits_no_update ON quality_trend_audits;
CREATE TRIGGER quality_trend_audits_no_update
BEFORE UPDATE ON quality_trend_audits
FOR EACH ROW EXECUTE FUNCTION reject_quality_trend_audit_mutation();
DROP TRIGGER IF EXISTS quality_trend_audits_no_delete ON quality_trend_audits;
CREATE TRIGGER quality_trend_audits_no_delete
BEFORE DELETE ON quality_trend_audits
FOR EACH ROW EXECUTE FUNCTION reject_quality_trend_audit_mutation();
DROP TRIGGER IF EXISTS quality_trend_audits_no_truncate ON quality_trend_audits;
CREATE TRIGGER quality_trend_audits_no_truncate
BEFORE TRUNCATE ON quality_trend_audits
FOR EACH STATEMENT EXECUTE FUNCTION reject_quality_trend_audit_mutation();
CREATE OR REPLACE FUNCTION reject_terminal_workflow_job_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION 'workflow jobs are immutable evidence';
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF OLD.terminal_outcome_json IS NOT NULL
       OR OLD.recovery_point = 'terminal'
       OR OLD.status IN (
            'accepted', 'blocked', 'cancelled', 'completed',
            'denied', 'failed'
          )
    THEN
      RAISE EXCEPTION 'terminal workflow job is immutable';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.terminal_outcome_json IS NOT NULL
     AND (
          NEW.job_id IS DISTINCT FROM OLD.job_id
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.task_id IS DISTINCT FROM OLD.task_id
       OR NEW.result_path IS DISTINCT FROM OLD.result_path
       OR NEW.status IS DISTINCT FROM OLD.status
       OR NEW.recovery_point IS DISTINCT FROM OLD.recovery_point
       OR NEW.terminal_status IS DISTINCT FROM OLD.terminal_status
       OR NEW.terminal_outcome_json IS DISTINCT FROM OLD.terminal_outcome_json
       OR NEW.terminal_outcome_recorded_at
            IS DISTINCT FROM OLD.terminal_outcome_recorded_at
       OR NEW.returncode IS DISTINCT FROM OLD.returncode
       OR NEW.error IS DISTINCT FROM OLD.error
       OR NEW.pid IS DISTINCT FROM OLD.pid
       OR NEW.worker_pgid IS DISTINCT FROM OLD.worker_pgid
       OR NEW.worker_started_at IS DISTINCT FROM OLD.worker_started_at
       OR NEW.worker_containment_id
            IS DISTINCT FROM OLD.worker_containment_id
     )
  THEN
    RAISE EXCEPTION 'terminal workflow job fields are immutable';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_terminal_freeze
  ON dual_agent_workflow_jobs;
CREATE TRIGGER dual_agent_workflow_jobs_terminal_freeze
BEFORE UPDATE ON dual_agent_workflow_jobs
FOR EACH ROW EXECUTE FUNCTION reject_terminal_workflow_job_mutation();
DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_terminal_no_delete
  ON dual_agent_workflow_jobs;
CREATE TRIGGER dual_agent_workflow_jobs_terminal_no_delete
BEFORE DELETE ON dual_agent_workflow_jobs
FOR EACH ROW EXECUTE FUNCTION reject_terminal_workflow_job_mutation();
DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_no_truncate
  ON dual_agent_workflow_jobs;
CREATE TRIGGER dual_agent_workflow_jobs_no_truncate
BEFORE TRUNCATE ON dual_agent_workflow_jobs
FOR EACH STATEMENT EXECUTE FUNCTION reject_terminal_workflow_job_mutation();
CREATE OR REPLACE FUNCTION reject_worker_reaped_at_rewrite()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.worker_reaped_at IS NOT NULL
     AND NEW.worker_reaped_at IS DISTINCT FROM OLD.worker_reaped_at
  THEN
    RAISE EXCEPTION 'worker_reaped_at is immutable once recorded';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_worker_reaped_once
  ON dual_agent_workflow_jobs;
CREATE TRIGGER dual_agent_workflow_jobs_worker_reaped_once
BEFORE UPDATE ON dual_agent_workflow_jobs
FOR EACH ROW EXECUTE FUNCTION reject_worker_reaped_at_rewrite();
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
    statements: list[str] = []
    current: list[str] = []
    single_quoted = False
    double_quoted = False
    dollar_tag: str | None = None
    index = 0
    while index < len(script):
        if dollar_tag is not None:
            if script.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue
            current.append(script[index])
            index += 1
            continue

        character = script[index]
        if not single_quoted and not double_quoted and character == "$":
            end = script.find("$", index + 1)
            if end != -1:
                candidate = script[index : end + 1]
                tag_body = candidate[1:-1]
                if not tag_body or tag_body.replace("_", "").isalnum():
                    dollar_tag = candidate
                    current.append(candidate)
                    index = end + 1
                    continue
        if character == "'" and not double_quoted:
            if single_quoted and index + 1 < len(script) and script[index + 1] == "'":
                current.extend(("'", "'"))
                index += 2
                continue
            single_quoted = not single_quoted
        elif character == '"' and not single_quoted:
            double_quoted = not double_quoted
        if character == ";" and not single_quoted and not double_quoted:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(character)
        index += 1
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def _event_payload(*, run_id: str, source: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return prepare_event_payload(
        run_id=run_id,
        source=source,
        kind=kind,
        payload=payload,
    )


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


def _quality_trend_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    for key in (
        "revision_rounds",
        "p11_audit_sample_size",
        "false_accept_count",
        "false_accept_denominator",
        "computed_at",
    ):
        payload[key] = int(payload.get(key) or 0)
    payload["accepted"] = bool(payload.get("accepted"))
    payload["first_pass_accepted"] = bool(payload.get("first_pass_accepted"))
    payload["false_accept_rate"] = float(payload.get("false_accept_rate") or 0.0)
    if payload.get("time_to_accepted_outcome_s") is not None:
        payload["time_to_accepted_outcome_s"] = float(payload["time_to_accepted_outcome_s"])
    details = payload.pop("details_json", {}) or {}
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            details = {}
    payload["details"] = details if isinstance(details, dict) else {}
    return payload


def _quality_trend_summary_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    run_count = int(row["run_count"] or 0)
    accepted_count = int(row["accepted_count"] or 0)
    first_pass_count = int(row["first_pass_accepted_count"] or 0)
    false_accept_denominator = int(row["false_accept_denominator"] or 0)
    false_accept_count = int(row["false_accept_count"] or 0)
    return {
        "task_class": row["task_class"],
        "gate": row["gate"],
        "policy_overlay_hashes": _split_group_concat(row.get("policy_overlay_hashes")),
        "policy_proposal_ids": _split_group_concat(row.get("policy_proposal_ids")),
        "run_count": run_count,
        "accepted_count": accepted_count,
        "acceptance_rate": accepted_count / run_count if run_count else 0.0,
        "first_pass_accepted_count": first_pass_count,
        "first_pass_acceptance_rate": first_pass_count / run_count if run_count else 0.0,
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


def _quality_trend_audit_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    for key in (
        "sample_size",
        "false_accept_count",
        "false_accept_denominator",
        "computed_at",
    ):
        payload[key] = int(payload.get(key) or 0)
    payload["false_accept_rate"] = float(payload.get("false_accept_rate") or 0.0)
    payload["audit_details"] = _as_payload(
        payload.pop("audit_details_json", {})
    )
    return payload


def _split_group_concat(value: Any) -> list[str]:
    return sorted({item for item in str(value or "").split(",") if item})


def _autoresearch_experiment_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["experiment"] = _as_payload(payload.pop("experiment_json", {}))
    payload["attempt"] = _as_payload(payload.pop("attempt_json", {}))
    payload["provenance"] = _as_payload(payload.pop("provenance_json", {}))
    payload["proposal_pointer"] = _as_payload(payload.pop("proposal_pointer_json", {}))
    for key in ("created_at", "updated_at", "activated_at", "last_run_started_at"):
        if payload.get(key) is not None:
            payload[key] = int(payload[key])
    return payload


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
        ledger_checkpoint_coordinator: (
            LedgerCheckpointCoordinator | None
        ) = None,
    ) -> None:
        psycopg, sql, row_helpers = _load_psycopg()
        dict_row, Jsonb = row_helpers
        self.db_path = dsn
        self.dsn = dsn
        self.schema = schema
        self._Jsonb = Jsonb
        self._sql = sql
        self._errors = psycopg.errors
        self._ledger_checkpoint_coordinator = ledger_checkpoint_coordinator
        self._conn = (connect or psycopg.connect)(dsn, row_factory=dict_row)
        self._conn.autocommit = True
        self._write_lock = threading.RLock()
        self.__evidence_commit_write_capability = object()
        self._lock = asyncio.Lock()
        if schema:
            self._conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            self._conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        if apply_schema:
            self.apply_schema()
        self.reconcile_event_checkpoints()

    @property
    def event_ledger_assurance(self) -> str:
        if getattr(self, "_ledger_checkpoint_coordinator", None) is None:
            return "diagnostic-only"
        return self._ledger_checkpoint_coordinator.assurance

    def apply_schema(self) -> None:
        with self._write_lock:
            with self._conn.transaction():
                schema_state = self._conn.execute(
                    """SELECT to_regclass('events') AS events_table,
                              to_regclass('alembic_version')
                                AS alembic_table"""
                ).fetchone()
                events_table = (
                    schema_state["events_table"]
                    if isinstance(schema_state, Mapping)
                    else schema_state[0]
                )
                alembic_table = (
                    schema_state["alembic_table"]
                    if isinstance(schema_state, Mapping)
                    else schema_state[1]
                )
                existing_alembic_version: str | None = None
                if alembic_table is not None:
                    version_row = self._conn.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchone()
                    if version_row is not None:
                        existing_alembic_version = str(
                            version_row["version_num"]
                            if isinstance(version_row, Mapping)
                            else version_row[0]
                        )
                if events_table is not None and alembic_table is None:
                    raise RuntimeError(
                        "existing Postgres schema is not Alembic-managed; "
                        "run `make migrate` before starting Supervisor"
                    )
                if (
                    alembic_table is not None
                    and existing_alembic_version != POSTGRES_ALEMBIC_HEAD
                ):
                    raise RuntimeError(
                        "Postgres schema is not at Alembic head "
                        f"{POSTGRES_ALEMBIC_HEAD}; run `make migrate` "
                        "before starting Supervisor"
                    )
                for statement in _split_sql_script(POSTGRES_SCHEMA_SQL):
                    self._conn.execute(statement)
                for statement in _split_sql_script(POSTGRES_EVENT_IMMUTABILITY_SQL):
                    self._conn.execute(statement)
                self._conn.execute(
                    """INSERT INTO schema_migrations(version, name, applied_at)
                       VALUES(1, 'postgres.event_job_lane', %s)
                       ON CONFLICT(version) DO NOTHING""",
                    (int(time.time()),),
                )
                self._conn.execute(
                    """INSERT INTO schema_migrations(version, name, applied_at)
                       VALUES(2, 'postgres.tamper_evident_event_ledger', %s)
                       ON CONFLICT(version) DO NOTHING""",
                    (int(time.time()),),
                )
                if alembic_table is None:
                    self._conn.execute(
                        """CREATE TABLE alembic_version (
                             version_num VARCHAR(32) NOT NULL,
                             CONSTRAINT alembic_version_pkc
                               PRIMARY KEY(version_num)
                           )"""
                    )
                    self._conn.execute(
                        """INSERT INTO alembic_version(version_num)
                           VALUES(%s)""",
                        (POSTGRES_ALEMBIC_HEAD,),
                    )

    def close(self) -> None:
        self._conn.close()

    # --- events ---
    def _next_stream_event_id(
        self,
        run_id: str,
    ) -> tuple[int, int | None, int, str | None]:
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
        previous = self._conn.execute(
            """SELECT event_sequence, event_hash
                 FROM events
                WHERE run_id=%s
                ORDER BY event_sequence DESC
                LIMIT 1""",
            (run_id,),
        ).fetchone()
        event_sequence = (
            int(previous["event_sequence"]) + 1
            if previous is not None
            else 1
        )
        previous_hash = (
            str(previous["event_hash"])
            if previous is not None and previous["event_hash"] is not None
            else None
        )
        if previous_id is not None and previous_hash is None:
            raise RuntimeError(
                "event stream predecessor is missing or unhashed: "
                f"run_id={run_id} event_id={previous_id}"
            )
        return event_id, previous_id, event_sequence, previous_hash

    def _insert_event_unlocked(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        payload: dict[str, Any],
        ts: int | None = None,
    ) -> int:
        (
            event_id,
            previous_id,
            event_sequence,
            previous_hash,
        ) = self._next_stream_event_id(run_id)
        event_ts = int(time.time()) if ts is None else int(ts)
        event_payload = _event_payload(
            run_id=run_id,
            source=source,
            kind=kind,
            payload=payload,
        )
        fields = build_ledger_fields(
            run_id=run_id,
            event_sequence=event_sequence,
            ts=event_ts,
            source=source,
            kind=kind,
            payload=event_payload,
            previous_event_hash=previous_hash,
            ledger_genesis_kind=NATIVE_GENESIS if previous_hash is None else None,
        )
        self._conn.execute(
            """INSERT INTO events(
                 run_id, event_id, event_sequence, previous_event_id, ts,
                 source, kind, payload_json,
                 previous_event_hash, event_hash, canonical_payload_hash,
                 artifact_manifest_hash, ledger_genesis_kind)
               VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                run_id,
                event_id,
                event_sequence,
                previous_id,
                event_ts,
                source,
                kind,
                self._Jsonb(event_payload),
                fields.previous_event_hash,
                fields.event_hash,
                fields.canonical_payload_hash,
                fields.artifact_manifest_hash,
                fields.ledger_genesis_kind,
            ),
        )
        return event_id

    def _event_ledger_rows(self, run_id: str) -> list[dict[str, Any]]:
        return list(
            self._conn.execute(
                """SELECT event_id, run_id, event_sequence, ts, source, kind,
                          payload_json,
                          previous_event_hash, event_hash,
                          canonical_payload_hash, artifact_manifest_hash,
                          ledger_genesis_kind
                     FROM events
                    WHERE run_id=%s
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
        coordinator = getattr(
            self,
            "_ledger_checkpoint_coordinator",
            None,
        )
        if coordinator is None or event_id <= 0:
            return
        event = self._conn.execute(
            """SELECT event_id, event_sequence
                 FROM events
                WHERE run_id=%s AND event_id=%s""",
            (run_id, event_id),
        ).fetchone()
        if event is None:
            raise RuntimeError(
                "committed event disappeared before checkpoint coordination"
            )
        coordinator.coordinate_event(
            run_id=run_id,
            event_id=int(event["event_id"]),
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
            getattr(self, "_ledger_checkpoint_coordinator", None),
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
        """Recover external checkpoint publication from committed events.

        Only events beyond each run's trusted checkpoint head are replayed;
        earlier events are already covered by an externally pinned
        checkpoint and checkpoint publication is idempotent.
        """
        coordinator = getattr(
            self,
            "_ledger_checkpoint_coordinator",
            None,
        )
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
                        WHERE run_id=%s AND event_sequence>%s
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
        payload: dict[str, Any],
        ts: int | None = None,
    ) -> int:
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
                    WHERE c.run_id=%s
                      AND c.kind=%s
                      AND c.idempotency_key=%s""",
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
        """Append one exact logical event across concurrent writers."""
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
        prepared_payload = _event_payload(
            run_id=run_id,
            source=source,
            kind=kind,
            payload=payload,
        )
        payload_sha256 = canonical_payload_hash(prepared_payload)
        with self._write_lock:
            with self._conn.transaction():
                claimed = self._conn.execute(
                    """INSERT INTO event_idempotency_claims(
                         run_id, kind, idempotency_key, event_id, source,
                         payload_sha256, created_at)
                       VALUES(%s, %s, %s, NULL, %s, %s, %s)
                       ON CONFLICT(run_id, kind, idempotency_key)
                       DO NOTHING
                       RETURNING idempotency_key""",
                    (
                        str(run_id),
                        str(kind),
                        normalized_key,
                        str(source),
                        payload_sha256,
                        int(time.time()),
                    ),
                ).fetchone()
                if claimed is not None:
                    event_id = self._insert_event_unlocked(
                        run_id=run_id,
                        source=source,
                        kind=kind,
                        payload=payload,
                        ts=ts,
                    )
                    finalized = self._conn.execute(
                        """UPDATE event_idempotency_claims
                              SET event_id=%s
                            WHERE run_id=%s AND kind=%s
                              AND idempotency_key=%s
                              AND event_id IS NULL
                          RETURNING event_id""",
                        (
                            event_id,
                            str(run_id),
                            str(kind),
                            normalized_key,
                        ),
                    ).fetchone()
                    if finalized is None:
                        raise RuntimeError(
                            "event idempotency claim finalization failed"
                        )
                else:
                    existing = self._conn.execute(
                        """SELECT event_id, source, payload_sha256
                             FROM event_idempotency_claims
                            WHERE run_id=%s AND kind=%s
                              AND idempotency_key=%s""",
                        (str(run_id), str(kind), normalized_key),
                    ).fetchone()
                    if existing is None or existing["event_id"] is None:
                        raise RuntimeError(
                            "event idempotency claim is incomplete"
                        )
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
                            WHERE run_id=%s AND event_id=%s AND kind=%s""",
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
        if not str(kind).startswith("historical_operation."):
            raise ValueError(
                "dedicated historical writer only accepts historical events"
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
            with self._conn.transaction():
                event_id = self._insert_event_unlocked(
                    run_id=run_id,
                    source=source,
                    kind=kind,
                    payload=payload,
                    ts=ts,
                )
            self._coordinate_committed_event(
                run_id=run_id,
                event_id=event_id,
                event_kind=kind,
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
        ts: int | None = None,
    ) -> int:
        assert_generic_event_kind_allowed(kind)
        assert_public_event_kind_allowed(kind)
        with self._write_lock:
            with self._conn.transaction():
                event_id = self._insert_event_unlocked(
                    run_id=run_id,
                    source=source,
                    kind=kind,
                    payload=payload,
                    ts=ts,
                )
                now = int(time.time())
                self._conn.execute(
                    """INSERT INTO tail_offsets(path, byte_offset, updated_at)
                       VALUES(%s, %s, %s)
                       ON CONFLICT(path) DO UPDATE
                         SET byte_offset=EXCLUDED.byte_offset,
                             updated_at=EXCLUDED.updated_at""",
                    (path, int(byte_offset), now),
                )
            self._coordinate_committed_event(
                run_id=run_id,
                event_id=event_id,
                event_kind=kind,
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
            """SELECT event_id, event_sequence, previous_event_id, run_id, ts,
                      source, kind, payload_json, previous_event_hash, event_hash,
                      canonical_payload_hash, artifact_manifest_hash,
                      ledger_genesis_kind
               FROM events
               WHERE run_id=%s AND event_id > %s
               ORDER BY event_id ASC
               LIMIT %s""",
            (run_id, int(after_event_id or 0), page_limit),
        ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "run_id": row["run_id"],
                "event_sequence": int(row["event_sequence"]),
                "previous_event_id": (
                    None if row["previous_event_id"] is None else int(row["previous_event_id"])
                ),
                "ts": int(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                "payload": _as_payload(row["payload_json"]),
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
        """Verify release evidence against an externally pinned signed head."""
        rows = self._event_ledger_rows(run_id)
        if (
            checkpoint_store is None
            and verifier is None
            and trusted_latest_checkpoint is None
            and getattr(
                self,
                "_ledger_checkpoint_coordinator",
                None,
            )
            is not None
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
        rows = self._conn.execute(
            """SELECT event_id, run_id, event_sequence, ts, source, kind,
                      payload_json,
                      previous_event_hash, event_hash,
                      canonical_payload_hash, artifact_manifest_hash,
                      ledger_genesis_kind
                 FROM events
                WHERE run_id=%s
                ORDER BY event_id ASC""",
            (run_id,),
        ).fetchall()
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
        ).fetchall()
        return [
            {
                **row,
                "payload_json": _payload_json_text(row["payload_json"]),
            }
            for row in rows
        ]

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
            with self._conn.transaction():
                row = self._conn.execute(
                    """INSERT INTO supervisor_lessons(
                         lesson_id, task_class, gate, taxonomy_code, root_cause,
                         remediation, source_run_id, normalized_key, created_at)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(lesson_id) DO NOTHING
                       RETURNING *""",
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
                ).fetchone()
                created = row is not None
                if row is None:
                    row = self._conn.execute(
                        """UPDATE supervisor_lessons
                              SET observed_count=observed_count + 1
                            WHERE lesson_id=%s
                            RETURNING *""",
                        (lesson_id,),
                    ).fetchone()
                if row is None:
                    raise RuntimeError("supervisor lesson was not persisted")
                return dict(row), created

    def query_supervisor_lessons(
        self,
        *,
        task_class: str,
        gate: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM supervisor_lessons
               WHERE task_class=%s AND gate=%s AND retired_at IS NULL
               ORDER BY created_at DESC, lesson_id ASC
               LIMIT %s""",
            (str(task_class or "general"), str(gate or "unknown"), int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_supervisor_lessons(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM supervisor_lessons
               ORDER BY created_at DESC, lesson_id ASC
               LIMIT %s""",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]

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
            with self._conn.transaction():
                for lesson_id in lesson_ids:
                    row = self._conn.execute(
                        "SELECT taxonomy_code FROM supervisor_lessons WHERE lesson_id=%s",
                        (str(lesson_id),),
                    ).fetchone()
                    if row is None:
                        continue
                    recurs = str(row["taxonomy_code"]) in recurring
                    self._conn.execute(
                        """UPDATE supervisor_lessons
                              SET injection_count=injection_count + 1,
                                  recurrence_count=recurrence_count + %s,
                                  retired_at=CASE
                                    WHEN retired_at IS NULL
                                     AND injection_count + 1 >= %s
                                     AND recurrence_count + %s >= %s
                                    THEN %s
                                    ELSE retired_at
                                  END
                            WHERE lesson_id=%s""",
                        (
                            1 if recurs else 0,
                            int(retire_after),
                            1 if recurs else 0,
                            int(retire_after),
                            now,
                            str(lesson_id),
                        ),
                    )

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
        event_id = 0
        with self._write_lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    """INSERT INTO supervisor_quality_trends(
                         run_id, task_id, task_class, gate, accepted,
                         first_pass_accepted, revision_rounds,
                         time_to_accepted_outcome_s, policy_overlay_hash,
                         policy_proposal_id, details_json, computed_at)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(run_id, gate) DO UPDATE SET
                         task_id=EXCLUDED.task_id,
                         task_class=EXCLUDED.task_class,
                         accepted=EXCLUDED.accepted,
                         first_pass_accepted=EXCLUDED.first_pass_accepted,
                         revision_rounds=EXCLUDED.revision_rounds,
                         time_to_accepted_outcome_s=EXCLUDED.time_to_accepted_outcome_s,
                         policy_overlay_hash=EXCLUDED.policy_overlay_hash,
                         policy_proposal_id=EXCLUDED.policy_proposal_id,
                         details_json=EXCLUDED.details_json,
                         computed_at=EXCLUDED.computed_at
                       RETURNING *""",
                    (
                        run_id,
                        task_id,
                        str(task_class or "unclassified"),
                        gate,
                        bool(accepted),
                        bool(first_pass_accepted),
                        int(revision_rounds),
                        time_to_accepted_outcome_s,
                        str(policy_overlay_hash or ""),
                        str(policy_proposal_id or ""),
                        self._Jsonb(redact(details or {})),
                        now,
                    ),
                ).fetchone()
                if row is not None:
                    projection_row = _quality_trend_row_to_dict(dict(row))
                    projection_row.pop("id", None)
                    event_id = self._insert_event_unlocked(
                        run_id=run_id,
                        source="quality_trends",
                        kind=QUALITY_TREND_PROJECTION_EVENT,
                        payload=quality_trend_projection_event_payload(
                            projection_row
                        ),
                    )
                if row is None:
                    raise RuntimeError("quality trend row was not persisted")
                result = _quality_trend_row_to_dict(dict(row))
            self._coordinate_committed_event(
                run_id=run_id,
                event_id=event_id,
                event_kind=QUALITY_TREND_PROJECTION_EVENT,
            )
            return result

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
        event_id = 0
        with self._write_lock:
            with self._conn.transaction():
                existing = self._conn.execute(
                    """SELECT details_json
                       FROM supervisor_quality_trends
                       WHERE run_id=%s AND gate=%s
                       FOR UPDATE""",
                    (run_id, gate),
                ).fetchone()
                if existing is None:
                    return None
                details = _as_payload(existing["details_json"])
                safe_audit_details = redact(audit_details or {})
                details["p11_audit"] = safe_audit_details
                latest = self._conn.execute(
                    """SELECT computed_at
                         FROM quality_trend_audits
                        WHERE run_id=%s AND gate=%s
                        ORDER BY computed_at DESC
                        LIMIT 1""",
                    (run_id, gate),
                ).fetchone()
                latest_computed_at = (
                    int(latest["computed_at"]) if latest is not None else -1
                )
                computed_at = max(int(time.time()), latest_computed_at + 1)
                self._conn.execute(
                    """INSERT INTO quality_trend_audits(
                         run_id, gate, sample_size, false_accept_count,
                         false_accept_denominator, false_accept_rate,
                         audit_details_json, computed_at)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        run_id,
                        gate,
                        sample,
                        false_count,
                        denominator,
                        rate,
                        self._Jsonb(safe_audit_details),
                        computed_at,
                    ),
                )
                row = self._conn.execute(
                    """UPDATE supervisor_quality_trends
                          SET p11_audit_sample_size=%s,
                              false_accept_count=%s,
                              false_accept_denominator=%s,
                              false_accept_rate=%s,
                              details_json=%s,
                              computed_at=%s
                        WHERE run_id=%s AND gate=%s
                        RETURNING *""",
                    (
                        sample,
                        false_count,
                        denominator,
                        rate,
                        self._Jsonb(details),
                        computed_at,
                        run_id,
                        gate,
                    ),
                ).fetchone()
                if row is not None:
                    projection_row = _quality_trend_row_to_dict(dict(row))
                    projection_row.pop("id", None)
                    event_id = self._insert_event_unlocked(
                        run_id=run_id,
                        source="quality_trends",
                        kind=QUALITY_TREND_PROJECTION_EVENT,
                        payload=quality_trend_projection_event_payload(
                            projection_row
                        ),
                    )
                result = (
                    _quality_trend_row_to_dict(dict(row))
                    if row is not None
                    else None
                )
            self._coordinate_committed_event(
                run_id=run_id,
                event_id=event_id,
                event_kind=QUALITY_TREND_PROJECTION_EVENT,
            )
            return result

    def list_quality_trend_audits(
        self,
        *,
        run_id: str | None = None,
        gate: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id=%s")
            params.append(run_id)
        if gate:
            clauses.append("gate=%s")
            params.append(gate)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT *
                  FROM quality_trend_audits
                  {where}
                 ORDER BY computed_at ASC, run_id ASC, gate ASC""",
            tuple(params),
        ).fetchall()
        return [
            _quality_trend_audit_row_to_dict(dict(row))
            for row in rows
        ]

    def query_quality_trends(
        self,
        *,
        task_class: str | None = None,
        gate: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_class:
            clauses.append("task_class=%s")
            params.append(task_class)
        if gate:
            clauses.append("gate=%s")
            params.append(gate)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT
                    task_class,
                    gate,
                    STRING_AGG(DISTINCT NULLIF(policy_overlay_hash, ''), ',') AS policy_overlay_hashes,
                    STRING_AGG(DISTINCT NULLIF(policy_proposal_id, ''), ',') AS policy_proposal_ids,
                    COUNT(*) AS run_count,
                    SUM(CASE WHEN accepted THEN 1 ELSE 0 END) AS accepted_count,
                    SUM(CASE WHEN first_pass_accepted THEN 1 ELSE 0 END) AS first_pass_accepted_count,
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
        return [_quality_trend_summary_to_dict(dict(row)) for row in rows]

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
            clauses.append("task_class=%s")
            params.append(task_class)
        if gate:
            clauses.append("gate=%s")
            params.append(gate)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT * FROM supervisor_quality_trends
                {where}
                ORDER BY computed_at ASC, run_id ASC, gate ASC""",
            tuple(params),
        ).fetchall()
        return [_quality_trend_row_to_dict(dict(row)) for row in rows]

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
            with self._conn.transaction():
                # Writers acquire the projection table before the stream
                # sequence and events tables. Lock in the same order so the
                # inventory, verified event cut, and replacement are one
                # serializable maintenance operation across processes.
                self._conn.execute(
                    "LOCK TABLE supervisor_quality_trends "
                    "IN SHARE ROW EXCLUSIVE MODE"
                )
                self._conn.execute(
                    "LOCK TABLE event_stream_sequences IN SHARE MODE"
                )
                self._conn.execute("LOCK TABLE events IN SHARE MODE")
                run_rows = self._conn.execute(
                    """SELECT DISTINCT run_id
                         FROM events
                        WHERE kind=%s
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
                    self._conn.execute(
                        """SELECT last_event_id
                             FROM event_stream_sequences
                            WHERE run_id=%s
                            FOR UPDATE""",
                        (run_id,),
                    ).fetchone()
                    rows = self._conn.execute(
                        """SELECT event_id, run_id, event_sequence, ts,
                                  source, kind, payload_json,
                                  previous_event_hash, event_hash,
                                  canonical_payload_hash,
                                  artifact_manifest_hash,
                                  ledger_genesis_kind
                             FROM events
                            WHERE run_id=%s
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
                            "payload": _as_payload(
                                row["payload_json"]
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
                               VALUES(%s, %s, %s, %s, %s, %s, %s, %s,
                                      %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (
                                row["run_id"],
                                row["task_id"],
                                row["task_class"],
                                row["gate"],
                                row["accepted"],
                                row["first_pass_accepted"],
                                row["revision_rounds"],
                                row["time_to_accepted_outcome_s"],
                                row["p11_audit_sample_size"],
                                row["false_accept_count"],
                                row["false_accept_denominator"],
                                row["false_accept_rate"],
                                row["policy_overlay_hash"],
                                row["policy_proposal_id"],
                                self._Jsonb(row["details"]),
                                row["computed_at"],
                            ),
                        )
        return rebuilt

    def list_p11_audit_candidate_run_ids(self, *, limit: int = 50) -> list[str]:
        rows = self._conn.execute(
            """SELECT run_id
                 FROM events
                WHERE kind='dual_agent_gate_result'
                  AND payload_json->>'gate' IN ('execution', 'outcome_review')
                  AND (
                    lower(COALESCE(payload_json->>'status', '')) IN ('accepted', 'accept')
                    OR lower(COALESCE(payload_json #>> '{outcome,decision}', '')) IN ('accepted', 'accept')
                  )
                GROUP BY run_id
                ORDER BY MAX(event_id) DESC
                LIMIT %s""",
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
            with self._conn.transaction():
                row = self._conn.execute(
                    """INSERT INTO supervisor_autoresearch_experiments(
                         experiment_id, signal_key, status, task_class, gate,
                         taxonomy_code, experiment_json, attempt_json, provenance_json,
                         report_only_reason, proposal_pointer_json, created_at, updated_at)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(signal_key) DO NOTHING
                       RETURNING *""",
                    (
                        experiment_id,
                        signal_key,
                        status,
                        task_class,
                        gate,
                        taxonomy_code,
                        self._Jsonb(redact(experiment)),
                        self._Jsonb(redact(attempt)),
                        self._Jsonb(redact(provenance)),
                        report_only_reason,
                        self._Jsonb(redact(proposal_pointer or {})),
                        now,
                        now,
                    ),
                ).fetchone()
                created = row is not None
                if row is None:
                    row = self._conn.execute(
                        """SELECT * FROM supervisor_autoresearch_experiments
                           WHERE signal_key=%s""",
                        (signal_key,),
                    ).fetchone()
                if row is None:
                    raise RuntimeError("AutoResearch experiment draft was not persisted")
                return _autoresearch_experiment_row_to_dict(dict(row)), created

    def get_autoresearch_experiment(self, *, experiment_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT * FROM supervisor_autoresearch_experiments
               WHERE experiment_id=%s""",
            (experiment_id,),
        ).fetchone()
        return _autoresearch_experiment_row_to_dict(dict(row)) if row is not None else None

    def list_autoresearch_experiment_queue(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status=%s"
            params.append(status)
        params.append(int(limit))
        rows = self._conn.execute(
            f"""SELECT * FROM supervisor_autoresearch_experiments
                {where}
                ORDER BY created_at ASC, experiment_id ASC
                LIMIT %s""",
            tuple(params),
        ).fetchall()
        return [_autoresearch_experiment_row_to_dict(dict(row)) for row in rows]

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
            with self._conn.transaction():
                row = self._conn.execute(
                    """SELECT * FROM supervisor_autoresearch_experiments
                       WHERE experiment_id=%s""",
                    (experiment_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
                if row["status"] == "draft":
                    row = self._conn.execute(
                        """UPDATE supervisor_autoresearch_experiments
                              SET status='runnable',
                                  activated_at=%s,
                                  activated_by=%s,
                                  activation_channel=%s,
                                  updated_at=%s
                            WHERE experiment_id=%s
                            RETURNING *""",
                        (now, operator, approval_channel, now, experiment_id),
                    ).fetchone()
                return _autoresearch_experiment_row_to_dict(dict(row))

    def park_autoresearch_experiment(
        self,
        *,
        experiment_id: str,
        parked_at: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time()) if parked_at is None else int(parked_at)
        with self._write_lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    """SELECT * FROM supervisor_autoresearch_experiments
                       WHERE experiment_id=%s""",
                    (experiment_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
                if row["status"] in {"draft", "runnable"}:
                    row = self._conn.execute(
                        """UPDATE supervisor_autoresearch_experiments
                              SET status='parked',
                                  updated_at=%s
                            WHERE experiment_id=%s
                            RETURNING *""",
                        (now, experiment_id),
                    ).fetchone()
                if row is None:
                    raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
                return _autoresearch_experiment_row_to_dict(dict(row))

    def mark_autoresearch_experiment_run_started(
        self,
        *,
        experiment_id: str,
        run_id: str,
        started_at: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time()) if started_at is None else int(started_at)
        with self._write_lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    """UPDATE supervisor_autoresearch_experiments
                          SET status='running',
                              last_run_id=%s,
                              last_run_started_at=%s,
                              updated_at=%s
                        WHERE experiment_id=%s AND status='runnable'
                        RETURNING *""",
                    (run_id, now, now, experiment_id),
                ).fetchone()
                if row is None:
                    row = self._conn.execute(
                        """SELECT * FROM supervisor_autoresearch_experiments
                           WHERE experiment_id=%s""",
                        (experiment_id,),
                    ).fetchone()
                if row is None:
                    raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
                return _autoresearch_experiment_row_to_dict(dict(row))

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
            with self._conn.transaction():
                row = self._conn.execute(
                    """UPDATE supervisor_autoresearch_experiments
                          SET status=%s,
                              report_ref=%s,
                              report_sha256=%s,
                              updated_at=%s
                        WHERE experiment_id=%s
                        RETURNING *""",
                    (status, report_ref, report_sha256, now, experiment_id),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"AutoResearch experiment not found: {experiment_id}")
                return _autoresearch_experiment_row_to_dict(dict(row))

    def count_autoresearch_experiments_started_since(self, *, started_since: int) -> int:
        row = self._conn.execute(
            """SELECT COUNT(*) AS count
                 FROM supervisor_autoresearch_experiments
                WHERE last_run_started_at IS NOT NULL
                  AND last_run_started_at >= %s""",
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
                   OR kind LIKE '%%probe_cohort%%'
                ORDER BY event_id ASC
                LIMIT %s""",
            (int(limit),),
        ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "run_id": row["run_id"],
                "ts": int(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                "payload": _as_payload(row["payload_json"]),
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
                ORDER BY global_id ASC
                LIMIT %s""",
            (int(limit),),
        ).fetchall()
        events: list[dict[str, Any]] = []
        expected = str(proposal_id or "").strip()
        for row in rows:
            payload = _as_payload(row["payload_json"])
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

    # --- historical evaluation operation coordination ---
    def reserve_historical_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        operation: str,
    ) -> tuple[dict[str, Any], bool]:
        now = int(time.time())
        with self._write_lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    """INSERT INTO historical_operation_claims(
                         operation_id, request_hash, operation, status,
                         terminal_event_id, created_at, updated_at)
                       VALUES(%s, %s, %s, 'running', NULL, %s, %s)
                       ON CONFLICT(operation_id) DO NOTHING
                       RETURNING *""",
                    (
                        operation_id,
                        request_hash,
                        operation,
                        now,
                        now,
                    ),
                ).fetchone()
                if row is not None:
                    return dict(row), True
                existing = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=%s""",
                    (operation_id,),
                ).fetchone()
                if existing is None:
                    raise RuntimeError(
                        "historical operation idempotency conflict had no "
                        "visible row"
                    )
                return dict(existing), False

    def complete_historical_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        status: str,
        terminal_event_id: int,
    ) -> int:
        if status not in {"completed", "failed"}:
            raise ValueError(
                "historical operation terminal status must be completed or failed"
            )
        now = int(time.time())
        with self._write_lock:
            with self._conn.transaction():
                claim = self._conn.execute(
                    """SELECT request_hash, operation
                         FROM historical_operation_claims
                        WHERE operation_id=%s""",
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
                        WHERE run_id=%s AND event_id=%s""",
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
                payload = _as_payload(event["payload_json"])
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
                        WHERE run_id=%s AND event_id=%s""",
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
                requested_payload = _as_payload(
                    requested["payload_json"]
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
                row = self._conn.execute(
                    """UPDATE historical_operation_claims
                          SET status=%s, terminal_event_id=%s, updated_at=%s
                        WHERE operation_id=%s
                          AND request_hash=%s
                          AND status='running'
                      RETURNING *""",
                    (
                        status,
                        int(terminal_event_id),
                        now,
                        operation_id,
                        request_hash,
                    ),
                ).fetchone()
                if row is not None:
                    return 1
                existing = self._conn.execute(
                    """SELECT * FROM historical_operation_claims
                       WHERE operation_id=%s""",
                    (operation_id,),
                ).fetchone()
                if existing is None:
                    raise KeyError(
                        f"historical operation not found: {operation_id}"
                    )
                if (
                    str(existing["request_hash"]) == request_hash
                    and str(existing["status"]) == status
                    and int(existing["terminal_event_id"] or 0)
                    == int(terminal_event_id)
                ):
                    return 0
                raise RuntimeError(
                    "historical operation terminal compare-and-set failed: "
                    f"{operation_id}"
                )

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
        worker_pgid: int | None = None,
        worker_started_at: float | None = None,
        worker_containment_id: str | None = None,
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
                existing = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=%s
                        FOR UPDATE""",
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
                         worker_containment_id, request_path,
                         result_path, log_path, idempotency_token, recovery_point,
                         request_payload_json, config_path, returncode, error,
                         created_at, updated_at)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(job_id) DO UPDATE SET
                         status=EXCLUDED.status,
                         pid=EXCLUDED.pid,
                         worker_pgid=EXCLUDED.worker_pgid,
                         worker_started_at=EXCLUDED.worker_started_at,
                         worker_containment_id=EXCLUDED.worker_containment_id,
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

    def update_dual_agent_workflow_job(self, *, job_id: str, **kwargs: Any) -> None:
        if kwargs.get("worker_reaped_at") is not None:
            raise RuntimeError(
                "worker_reaped_at may only be recorded through a "
                "containment-verified reap API"
            )
        now = int(time.time())
        assignments = ["updated_at=%s"]
        params: list[Any] = [now]
        clear_lease = bool(kwargs.pop("clear_lease", False))
        clear_next_dispatch_at = bool(kwargs.pop("clear_next_dispatch_at", False))
        for key in (
            "status",
            "pid",
            "worker_pgid",
            "worker_started_at",
            "worker_containment_id",
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
                existing = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=%s
                        FOR UPDATE""",
                    (job_id,),
                ).fetchone()
                attempted: dict[str, Any] = {}
                for field in (
                    "status",
                    "pid",
                    "worker_pgid",
                    "worker_started_at",
                    "worker_containment_id",
                    "returncode",
                    "error",
                    "recovery_point",
                ):
                    if field in kwargs and kwargs[field] is not None:
                        attempted[field] = kwargs[field]
                assert_terminal_workflow_job_mutation_allowed(
                    existing,
                    attempted=attempted,
                )
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
                existing = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=%s
                        FOR UPDATE""",
                    (job_id,),
                ).fetchone()
                assert_terminal_workflow_job_mutation_allowed(
                    existing,
                    attempted={} if error is None else {"error": error},
                )
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
    ) -> dict[str, Any] | None:
        now_value = int(now)
        claim_expires_at = now_value + max(1, int(lease_ttl_s))
        with self._write_lock:
            with self._conn.transaction():
                return self._conn.execute(
                    POSTGRES_CLAIM_WORKFLOW_JOB_FOR_REAP_SQL,
                    {
                        "job_id": job_id,
                        "reaper_id": reaper_id,
                        "claim_expires_at": claim_expires_at,
                        "now": now_value,
                        "expected_leased_by": expected_leased_by,
                        "expected_lease_expires_at": (
                            expected_lease_expires_at
                        ),
                        "expected_heartbeat_at": expected_heartbeat_at,
                        "expected_pid": expected_pid,
                        "expected_worker_pgid": expected_worker_pgid,
                        "expected_worker_started_at": (
                            expected_worker_started_at
                        ),
                        "expected_worker_containment_id": (
                            expected_worker_containment_id
                        ),
                    },
                ).fetchone()

    def park_dual_agent_workflow_job(self, *, job_id: str, reason: str) -> dict[str, Any] | None:
        now = int(time.time())
        with self._write_lock:
            with self._conn.transaction():
                existing = self._conn.execute(
                    """SELECT *
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=%s
                        FOR UPDATE""",
                    (job_id,),
                ).fetchone()
                assert_terminal_workflow_job_mutation_allowed(
                    existing,
                    attempted={"status": "parked", "error": reason},
                )
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

    def list_terminal_dual_agent_workflow_jobs_pending_reap(
        self,
    ) -> list[dict[str, Any]]:
        return list(
            self._conn.execute(
                """SELECT *
                     FROM dual_agent_workflow_jobs
                    WHERE terminal_outcome_json IS NOT NULL
                      AND pid IS NOT NULL
                      AND worker_reaped_at IS NULL
                    ORDER BY updated_at ASC, job_id ASC"""
            ).fetchall()
        )

    def record_dual_agent_workflow_worker_reaped(
        self,
        *,
        job_id: str,
        worker_reaped_at: int,
        termination: dict[str, Any],
    ) -> int:
        reaped_at = int(worker_reaped_at)
        if (
            not isinstance(termination, dict)
            or termination.get("safe_to_finalize") is not True
        ):
            raise RuntimeError(
                "worker reap requires a successful containment termination proof"
            )
        run_id = ""
        event_id = 0
        with self._write_lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    """SELECT job_id, run_id, task_id, pid, worker_pgid,
                              worker_started_at, worker_containment_id,
                              worker_reaped_at
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=%s
                        FOR UPDATE""",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"workflow job not found: {job_id}")
                if row["pid"] is None:
                    raise RuntimeError(
                        "cannot record a worker reap for an in-process job"
                    )
                if row["worker_reaped_at"] is not None:
                    if int(row["worker_reaped_at"]) != reaped_at:
                        raise RuntimeError(
                            "worker_reaped_at is immutable once recorded"
                        )
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
                cursor = self._conn.execute(
                    """UPDATE dual_agent_workflow_jobs
                          SET worker_reaped_at=%s,
                              updated_at=%s
                        WHERE job_id=%s
                          AND worker_reaped_at IS NULL""",
                    (reaped_at, int(time.time()), job_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"worker reap compare-and-set failed: {job_id}"
                    )
                run_id = str(row["run_id"])
                event_id = self._insert_event_unlocked(
                    run_id=run_id,
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
                        "worker_reaped_at": reaped_at,
                        "termination": termination,
                        "transport_recovery": "detached_cli_worker",
                    },
                    ts=reaped_at,
                )
            self._coordinate_committed_event(
                run_id=run_id,
                event_id=event_id,
                event_kind="dual_agent_workflow_worker_reaped",
            )
            return event_id

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
        worker_reaped_at: int | None = None,
        termination: dict[str, Any] | None = None,
    ) -> int:
        if not isinstance(terminal_outcome, dict) or not terminal_outcome:
            raise ValueError("terminal_outcome must be a non-empty dict")
        now = int(time.time())
        event_id = 0
        discrepancy = False
        with self._write_lock:
            with self._conn.transaction():
                row = self._conn.execute(
                    """SELECT job_id, run_id, task_id, result_path, status,
                              pid, worker_pgid, worker_started_at,
                              worker_containment_id, worker_reaped_at,
                              recovery_point, terminal_status,
                              terminal_outcome_json, terminal_outcome_recorded_at,
                              returncode, error
                         FROM dual_agent_workflow_jobs
                        WHERE job_id=%s
                        FOR UPDATE""",
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
                elif row["pid"] is None and worker_reaped_at is not None:
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
                                WHERE run_id=%s
                                  AND kind='dual_agent_workflow_terminal_outcome'
                                  AND payload_json->>'job_id'=%s
                                ORDER BY event_id DESC
                                LIMIT 1""",
                            (str(row["run_id"]), job_id),
                        ).fetchone()
                        if terminal_event is not None:
                            self._coordinate_committed_event(
                                run_id=str(row["run_id"]),
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
                    discrepancy_payload = {
                            "job_id": job_id,
                            "task_id": row["task_id"],
                            "result_path": row["result_path"],
                            "original_status": row["status"],
                            "original_terminal_status": row["terminal_status"],
                            "original_terminal_outcome": json.loads(
                                str(existing_outcome_json)
                            ),
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
                    }
                    existing_discrepancy = self._conn.execute(
                        """SELECT event_id
                             FROM events
                            WHERE run_id=%s
                              AND kind='dual_agent_workflow_terminal_discrepancy'
                              AND payload_json->>'conflict_sha256'=%s
                            LIMIT 1""",
                        (str(row["run_id"]), conflict_sha256),
                    ).fetchone()
                    if existing_discrepancy is None:
                        event_id = self._insert_event_unlocked(
                            run_id=str(row["run_id"]),
                            source="dual_agent",
                            kind="dual_agent_workflow_terminal_discrepancy",
                            payload=discrepancy_payload,
                            ts=now,
                        )
                    else:
                        event_id = int(existing_discrepancy["event_id"])
                    discrepancy = True
                else:
                    cursor = self._conn.execute(
                        """UPDATE dual_agent_workflow_jobs
                              SET status=%s,
                                  recovery_point='terminal',
                                  recovery_claim_token=NULL,
                                  recovery_claimed_at=NULL,
                                  leased_by=NULL,
                                  lease_expires_at=NULL,
                                  heartbeat_at=NULL,
                                  worker_reaped_at=%s,
                                  terminal_status=%s,
                                  terminal_outcome_json=%s,
                                  terminal_outcome_recorded_at=%s,
                                  returncode=%s,
                                  error=%s,
                                  updated_at=%s
                            WHERE job_id=%s
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
                            "workflow job terminal completion compare-and-set "
                            f"failed: {job_id}"
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
                        self._insert_event_unlocked(
                            run_id=str(row["run_id"]),
                            source="dual_agent",
                            kind="dual_agent_workflow_worker_reaped",
                            payload={
                                "job_id": job_id,
                                "task_id": row["task_id"],
                                "pid": row["pid"],
                                "worker_pgid": row["worker_pgid"],
                                "worker_started_at": row[
                                    "worker_started_at"
                                ],
                                "worker_containment_id": row[
                                    "worker_containment_id"
                                ],
                                "worker_reaped_at": reaped_at_value,
                                "termination": termination,
                                "transport_recovery": (
                                    "detached_cli_worker"
                                ),
                            },
                            ts=now,
                        )
                    event_id = self._insert_event_unlocked(
                        run_id=str(row["run_id"]),
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
                                terminal_completion_record_sha256(
                                    terminal_record
                                )
                            ),
                            "transport_recovery": "detached_cli_worker",
                        },
                        ts=now,
                    )
                committed_run_id = str(row["run_id"])
                committed_event_kind = (
                    "dual_agent_workflow_terminal_discrepancy"
                    if discrepancy
                    else "dual_agent_workflow_terminal_outcome"
                )
        self._coordinate_committed_event(
            run_id=committed_run_id,
            event_id=event_id,
            event_kind=committed_event_kind,
        )
        if discrepancy:
            raise RuntimeError(
                f"workflow job terminal outcome discrepancy: {job_id}"
            )
        return event_id


__all__ = [
    "POSTGRES_CLAIM_AVAILABLE_JOBS_SQL",
    "POSTGRES_CLAIM_WORKFLOW_JOB_FOR_REAP_SQL",
    "POSTGRES_LOCK_ORDER",
    "POSTGRES_SCHEMA_SQL",
    "PostgresState",
]
