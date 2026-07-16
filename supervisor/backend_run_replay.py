"""Durable replay protection for attested execution-backend runs."""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


BACKEND_RUN_REPLAY_SCHEMA_VERSION = (
    "supervisor-swe-bench-backend-run-replay/v1"
)
VERIFICATION_ATTEMPT_SCHEMA_VERSION = (
    "supervisor-swe-bench-verification-attempt/v1"
)
VERIFICATION_ATTEMPT_KEY_SCHEMA_VERSION = (
    "supervisor-swe-bench-verification-attempt-key/v1"
)
REPLAY_AUTHORITY_SCHEMA_VERSION = (
    "supervisor-swe-bench-replay-authority/v1"
)
REPLAY_AUTHORITY_ANCHOR_SCHEMA_VERSION = (
    "supervisor-swe-bench-replay-authority-anchor/v1"
)
REPLAY_STATE_SCHEMA_VERSION = (
    "supervisor-swe-bench-replay-state/v1"
)
_REPLAY_TABLE_NAME = "swe_bench_backend_run_consumptions"
_VERIFICATION_ATTEMPT_TABLE_NAME = "swe_bench_verification_attempts"
_AUTHORITY_TABLE_NAME = "swe_bench_replay_authority"
_STATE_TABLE_NAME = "swe_bench_replay_state"
_MAX_AUTHORITY_ANCHOR_BYTES = 4096
_AUTHORITY_ANCHOR_READ_ATTEMPTS = 4
_CANONICAL_AUTHORITY_TABLE_SQL = """
CREATE TABLE swe_bench_replay_authority(
  schema_version TEXT NOT NULL CHECK(
    schema_version =
    'supervisor-swe-bench-replay-authority/v1'
  ),
  singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton = 1),
  authority_id TEXT NOT NULL UNIQUE CHECK(
    length(authority_id) = 64
    AND authority_id = lower(authority_id)
    AND authority_id NOT GLOB '*[^0-9a-f]*'
  ),
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
)
""".strip()
_CANONICAL_AUTHORITY_TRIGGER_SQL = (
    """
    CREATE TRIGGER swe_bench_replay_authority_no_replace
    BEFORE INSERT ON swe_bench_replay_authority
    WHEN EXISTS(
      SELECT 1 FROM swe_bench_replay_authority
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay authority is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_replay_authority_no_update
    BEFORE UPDATE ON swe_bench_replay_authority
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay authority is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_replay_authority_no_delete
    BEFORE DELETE ON swe_bench_replay_authority
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay authority is immutable'
      );
    END
    """.strip(),
)
_CANONICAL_STATE_TABLE_SQL = """
CREATE TABLE swe_bench_replay_state(
  schema_version TEXT NOT NULL CHECK(
    schema_version =
    'supervisor-swe-bench-replay-state/v1'
  ),
  singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton = 1),
  generation INTEGER NOT NULL CHECK(generation >= 0),
  previous_state_hash TEXT,
  state_hash TEXT NOT NULL UNIQUE CHECK(
    length(state_hash) = 64
    AND state_hash = lower(state_hash)
    AND state_hash NOT GLOB '*[^0-9a-f]*'
  ),
  CHECK(
    (
      generation = 0
      AND previous_state_hash IS NULL
    )
    OR
    (
      generation > 0
      AND length(previous_state_hash) = 64
      AND previous_state_hash = lower(previous_state_hash)
      AND previous_state_hash NOT GLOB '*[^0-9a-f]*'
    )
  )
)
""".strip()
_CANONICAL_STATE_TRIGGER_SQL = (
    """
    CREATE TRIGGER swe_bench_replay_state_no_replace
    BEFORE INSERT ON swe_bench_replay_state
    WHEN EXISTS(
      SELECT 1 FROM swe_bench_replay_state
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay state is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_replay_state_advance_once
    BEFORE UPDATE ON swe_bench_replay_state
    WHEN NOT (
      OLD.schema_version = NEW.schema_version
      AND OLD.singleton = NEW.singleton
      AND NEW.generation = OLD.generation + 1
      AND NEW.previous_state_hash = OLD.state_hash
      AND NEW.state_hash <> OLD.state_hash
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay state must advance exactly once'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_replay_state_no_delete
    BEFORE DELETE ON swe_bench_replay_state
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay state is immutable'
      );
    END
    """.strip(),
)
_CANONICAL_REPLAY_TABLE_SQL = """
CREATE TABLE swe_bench_backend_run_consumptions(
  schema_version TEXT NOT NULL CHECK(
    schema_version =
    'supervisor-swe-bench-backend-run-replay/v1'
  ),
  backend_id TEXT NOT NULL CHECK(
    length(trim(backend_id)) > 0
  ),
  backend_run_id TEXT NOT NULL CHECK(
    length(trim(backend_run_id)) > 0
  ),
  authority_hash TEXT NOT NULL CHECK(
    length(authority_hash) = 64
    AND authority_hash = lower(authority_hash)
    AND authority_hash NOT GLOB '*[^0-9a-f]*'
  ),
  consumed_at_ms INTEGER NOT NULL CHECK(
    consumed_at_ms >= 0
  ),
  PRIMARY KEY(backend_id, backend_run_id)
)
""".strip()
_CANONICAL_REPLAY_TRIGGER_SQL = (
    """
    CREATE TRIGGER swe_bench_backend_run_consumptions_no_replace
    BEFORE INSERT ON swe_bench_backend_run_consumptions
    WHEN EXISTS(
      SELECT 1
        FROM swe_bench_backend_run_consumptions
       WHERE backend_id = NEW.backend_id
         AND backend_run_id = NEW.backend_run_id
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay consumption is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_backend_run_consumptions_no_update
    BEFORE UPDATE ON swe_bench_backend_run_consumptions
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay consumption is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_backend_run_consumptions_no_delete
    BEFORE DELETE ON swe_bench_backend_run_consumptions
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay consumption is immutable'
      );
    END
    """.strip(),
)
_CANONICAL_VERIFICATION_ATTEMPT_TABLE_SQL = """
CREATE TABLE swe_bench_verification_attempts(
  schema_version TEXT NOT NULL CHECK(
    schema_version =
    'supervisor-swe-bench-verification-attempt/v1'
  ),
  attempt_key TEXT NOT NULL PRIMARY KEY CHECK(
    length(attempt_key) = 64
    AND attempt_key = lower(attempt_key)
    AND attempt_key NOT GLOB '*[^0-9a-f]*'
  ),
  slot_key TEXT NOT NULL UNIQUE CHECK(
    length(slot_key) = 64
    AND slot_key = lower(slot_key)
    AND slot_key NOT GLOB '*[^0-9a-f]*'
  ),
  execution_spec_hash TEXT NOT NULL CHECK(
    length(execution_spec_hash) = 64
    AND execution_spec_hash = lower(execution_spec_hash)
    AND execution_spec_hash NOT GLOB '*[^0-9a-f]*'
  ),
  frozen_result_hash TEXT NOT NULL CHECK(
    length(frozen_result_hash) = 64
    AND frozen_result_hash = lower(frozen_result_hash)
    AND frozen_result_hash NOT GLOB '*[^0-9a-f]*'
  ),
  model_patch_sha256 TEXT NOT NULL CHECK(
    length(model_patch_sha256) = 64
    AND model_patch_sha256 = lower(model_patch_sha256)
    AND model_patch_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  producer_run_result_hash TEXT NOT NULL CHECK(
    length(producer_run_result_hash) = 64
    AND producer_run_result_hash = lower(producer_run_result_hash)
    AND producer_run_result_hash NOT GLOB '*[^0-9a-f]*'
  ),
  verifier_id TEXT NOT NULL CHECK(length(trim(verifier_id)) > 0),
  verifier_version TEXT NOT NULL CHECK(
    length(trim(verifier_version)) > 0
  ),
  verifier_hash TEXT NOT NULL CHECK(
    length(verifier_hash) = 64
    AND verifier_hash = lower(verifier_hash)
    AND verifier_hash NOT GLOB '*[^0-9a-f]*'
  ),
  verification_policy_hash TEXT NOT NULL CHECK(
    length(verification_policy_hash) = 64
    AND verification_policy_hash = lower(verification_policy_hash)
    AND verification_policy_hash NOT GLOB '*[^0-9a-f]*'
  ),
  context_json TEXT NOT NULL,
  context_hash TEXT NOT NULL CHECK(
    length(context_hash) = 64
    AND context_hash = lower(context_hash)
    AND context_hash NOT GLOB '*[^0-9a-f]*'
  ),
  requested_backend_id TEXT NOT NULL CHECK(
    length(trim(requested_backend_id)) > 0
  ),
  requested_backend_manifest_hash TEXT NOT NULL CHECK(
    length(requested_backend_manifest_hash) = 64
    AND requested_backend_manifest_hash =
      lower(requested_backend_manifest_hash)
    AND requested_backend_manifest_hash NOT GLOB '*[^0-9a-f]*'
  ),
  request_nonce TEXT NOT NULL UNIQUE CHECK(
    length(request_nonce) = 64
    AND request_nonce = lower(request_nonce)
    AND request_nonce NOT GLOB '*[^0-9a-f]*'
  ),
  prepared_hash TEXT NOT NULL CHECK(
    length(prepared_hash) = 64
    AND prepared_hash = lower(prepared_hash)
    AND prepared_hash NOT GLOB '*[^0-9a-f]*'
  ),
  state TEXT NOT NULL CHECK(state IN ('PREPARED', 'COMPLETED')),
  prepared_at_ms INTEGER NOT NULL CHECK(prepared_at_ms >= 0),
  completed_at_ms INTEGER,
  backend_run_id TEXT,
  authority_hash TEXT,
  grade_json TEXT,
  grade_hash TEXT,
  completion_hash TEXT,
  UNIQUE(requested_backend_id, backend_run_id),
  FOREIGN KEY(requested_backend_id, backend_run_id)
    REFERENCES swe_bench_backend_run_consumptions(
      backend_id, backend_run_id
    ),
  CHECK(
    (
      state = 'PREPARED'
      AND completed_at_ms IS NULL
      AND backend_run_id IS NULL
      AND authority_hash IS NULL
      AND grade_json IS NULL
      AND grade_hash IS NULL
      AND completion_hash IS NULL
    )
    OR
    (
      state = 'COMPLETED'
      AND completed_at_ms >= prepared_at_ms
      AND length(trim(backend_run_id)) > 0
      AND length(authority_hash) = 64
      AND authority_hash = lower(authority_hash)
      AND authority_hash NOT GLOB '*[^0-9a-f]*'
      AND grade_json IS NOT NULL
      AND length(grade_hash) = 64
      AND grade_hash = lower(grade_hash)
      AND grade_hash NOT GLOB '*[^0-9a-f]*'
      AND length(completion_hash) = 64
      AND completion_hash = lower(completion_hash)
      AND completion_hash NOT GLOB '*[^0-9a-f]*'
    )
  )
)
""".strip()
_CANONICAL_VERIFICATION_ATTEMPT_TRIGGER_SQL = (
    """
    CREATE TRIGGER swe_bench_verification_attempts_prepared_insert_only
    BEFORE INSERT ON swe_bench_verification_attempts
    WHEN NEW.state <> 'PREPARED'
    BEGIN
      SELECT RAISE(
        ABORT,
        'verification attempts must begin prepared'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_verification_attempts_no_replace
    BEFORE INSERT ON swe_bench_verification_attempts
    WHEN EXISTS(
      SELECT 1
        FROM swe_bench_verification_attempts
       WHERE attempt_key = NEW.attempt_key
          OR slot_key = NEW.slot_key
          OR request_nonce = NEW.request_nonce
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'verification attempt identity is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_verification_attempts_complete_once
    BEFORE UPDATE ON swe_bench_verification_attempts
    WHEN NOT (
      OLD.state = 'PREPARED'
      AND NEW.state = 'COMPLETED'
      AND NEW.schema_version = OLD.schema_version
      AND NEW.attempt_key = OLD.attempt_key
      AND NEW.slot_key = OLD.slot_key
      AND NEW.execution_spec_hash = OLD.execution_spec_hash
      AND NEW.frozen_result_hash = OLD.frozen_result_hash
      AND NEW.model_patch_sha256 = OLD.model_patch_sha256
      AND NEW.producer_run_result_hash =
        OLD.producer_run_result_hash
      AND NEW.verifier_id = OLD.verifier_id
      AND NEW.verifier_version = OLD.verifier_version
      AND NEW.verifier_hash = OLD.verifier_hash
      AND NEW.verification_policy_hash =
        OLD.verification_policy_hash
      AND NEW.context_json = OLD.context_json
      AND NEW.context_hash = OLD.context_hash
      AND NEW.requested_backend_id = OLD.requested_backend_id
      AND NEW.requested_backend_manifest_hash =
        OLD.requested_backend_manifest_hash
      AND NEW.request_nonce = OLD.request_nonce
      AND NEW.prepared_hash = OLD.prepared_hash
      AND NEW.prepared_at_ms = OLD.prepared_at_ms
      AND EXISTS(
        SELECT 1
          FROM swe_bench_backend_run_consumptions
         WHERE backend_id = NEW.requested_backend_id
           AND backend_run_id = NEW.backend_run_id
           AND authority_hash = NEW.authority_hash
      )
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'verification attempt update is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_verification_attempts_no_delete
    BEFORE DELETE ON swe_bench_verification_attempts
    BEGIN
      SELECT RAISE(
        ABORT,
        'verification attempts are immutable'
      );
    END
    """.strip(),
)


class BackendRunReplayGuardError(RuntimeError):
    """Base error for durable backend-run replay protection."""


class BackendRunReplayConflictError(BackendRunReplayGuardError):
    """A consumed backend run was presented with different authority."""


class BackendRunReplayCommittedDetachedError(
    BackendRunReplayGuardError
):
    """A transaction committed before its canonical locator was displaced."""


class VerificationAttemptConflictError(BackendRunReplayGuardError):
    """A durable verification attempt was retried with different authority."""


@dataclass(frozen=True)
class _ReplayState:
    generation: int
    previous_state_hash: str | None
    state_hash: str


@dataclass(frozen=True)
class _AuthorityAnchor:
    authority_id: str
    generation: int
    state_hash: str


@dataclass(frozen=True)
class VerificationAttemptSpec:
    """Immutable verifier input and recovery context for one attempt."""

    execution_spec_hash: str
    frozen_result_hash: str
    model_patch_sha256: str
    producer_run_result_hash: str
    verifier_id: str
    verifier_version: str
    verifier_hash: str
    verification_policy_hash: str
    slot_key: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "execution_spec_hash",
            "frozen_result_hash",
            "model_patch_sha256",
            "producer_run_result_hash",
            "verifier_hash",
            "verification_policy_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _canonical_sha256(
                    getattr(self, field_name),
                    field=field_name,
                ),
            )
        object.__setattr__(
            self,
            "verifier_id",
            _required_text(self.verifier_id, field="verifier_id"),
        )
        object.__setattr__(
            self,
            "verifier_version",
            _required_text(
                self.verifier_version,
                field="verifier_version",
            ),
        )
        canonical_context = _canonical_json_mapping(
            self.context,
            field="context",
        )
        object.__setattr__(
            self,
            "context",
            _freeze_json_value(canonical_context),
        )
        if self.slot_key:
            object.__setattr__(
                self,
                "slot_key",
                _canonical_sha256(self.slot_key, field="slot_key"),
            )

    @property
    def context_hash(self) -> str:
        return _sha256_json(self.context)

    @property
    def attempt_key(self) -> str:
        return _sha256_json(self.identity_dict())

    @property
    def effective_slot_key(self) -> str:
        if self.slot_key:
            return self.slot_key
        return _sha256_json({
            "schema_version": (
                "supervisor-swe-bench-verification-default-slot/v1"
            ),
            "execution_spec_hash": self.execution_spec_hash,
            "frozen_result_hash": self.frozen_result_hash,
            "model_patch_sha256": self.model_patch_sha256,
            "producer_run_result_hash": self.producer_run_result_hash,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "verifier_hash": self.verifier_hash,
        })

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VERIFICATION_ATTEMPT_KEY_SCHEMA_VERSION,
            **self._base_identity_dict(),
            "slot_key": self.effective_slot_key,
        }

    def _base_identity_dict(self) -> dict[str, Any]:
        return {
            "execution_spec_hash": self.execution_spec_hash,
            "frozen_result_hash": self.frozen_result_hash,
            "model_patch_sha256": self.model_patch_sha256,
            "producer_run_result_hash": self.producer_run_result_hash,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "verifier_hash": self.verifier_hash,
            "verification_policy_hash": self.verification_policy_hash,
            "context_hash": self.context_hash,
        }


@dataclass(frozen=True)
class VerificationAttemptRecord:
    """Recovered PREPARED or COMPLETED verification authority."""

    spec: VerificationAttemptSpec
    attempt_key: str
    slot_key: str
    state: str
    request_nonce: str
    requested_backend_id: str
    requested_backend_manifest_hash: str
    prepared_at_ms: int
    prepared_hash: str
    completed_at_ms: int | None = None
    backend_run_id: str | None = None
    authority_hash: str | None = None
    grade: Mapping[str, Any] | None = None
    grade_hash: str | None = None
    completion_hash: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"PREPARED", "COMPLETED"}:
            raise ValueError("verification attempt state is invalid")
        if self.attempt_key != self.spec.attempt_key:
            raise ValueError("verification attempt key mismatch")
        if self.slot_key != self.spec.effective_slot_key:
            raise ValueError("verification attempt slot mismatch")
        _canonical_sha256(self.request_nonce, field="request_nonce")
        _canonical_sha256(
            self.requested_backend_manifest_hash,
            field="requested_backend_manifest_hash",
        )
        _canonical_sha256(
            self.prepared_hash,
            field="prepared_hash",
        )
        _required_text(
            self.requested_backend_id,
            field="requested_backend_id",
        )
        if self.grade is not None:
            object.__setattr__(
                self,
                "grade",
                _freeze_json_value(
                    _canonical_json_mapping(self.grade, field="grade")
                ),
            )


class SQLiteBackendRunReplayGuard:
    """Atomically consume each backend execution identity exactly once."""

    @classmethod
    def provision(
        cls,
        path: str | Path,
    ) -> "SQLiteBackendRunReplayGuard":
        """Explicitly create one new authority and return it opened."""
        return cls(path, _provision=True)

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        expected_authority_id: str,
    ) -> "SQLiteBackendRunReplayGuard":
        """Open an existing authority without creating any filesystem state."""
        return cls(
            path,
            expected_authority_id=expected_authority_id,
        )

    def __init__(
        self,
        path: str | Path,
        *,
        expected_authority_id: str | None = None,
        _provision: bool = False,
    ) -> None:
        raw_path = str(path).strip()
        if not raw_path or raw_path == ":memory:":
            raise ValueError(
                "durable backend run replay guard requires a file path"
            )
        expanded = Path(raw_path).expanduser()
        if not expanded.is_absolute():
            raise ValueError(
                "durable backend run replay guard requires an absolute "
                "file path"
            )
        normalized = Path(os.path.normpath(str(expanded)))
        _reject_symlink_components(normalized)
        authority_anchor = _authority_anchor_path(normalized)
        provisioned_path = False
        if _provision:
            if expected_authority_id is not None:
                raise ValueError(
                    "provisioning cannot accept an existing authority id"
                )
            normalized.parent.mkdir(parents=True, exist_ok=True)
            _reject_symlink_components(normalized)
            if (
                _database_identity(normalized, missing_ok=True) is not None
                or _authority_anchor_exists(authority_anchor)
            ):
                raise BackendRunReplayGuardError(
                    "backend run replay provisioning requires an unused path"
                )
            _create_private_database_file(normalized)
            provisioned_path = True
            initial_identity = _database_identity(
                normalized,
                missing_ok=False,
            )
        else:
            if expected_authority_id is None:
                raise ValueError(
                    "opening a backend run replay journal requires the "
                    "expected external authority id"
                )
            expected_authority_id = _canonical_sha256(
                expected_authority_id,
                field="expected_authority_id",
            )
            _validate_sqlite_sidecar_security(
                normalized,
                repair=False,
            )
            initial_identity = _database_identity(
                normalized,
                missing_ok=False,
            )
        self.path = str(normalized)
        self.authority_anchor_path = str(authority_anchor)
        self._lock = threading.RLock()
        self._poisoned = False
        try:
            self._conn = sqlite3.connect(
                f"{normalized.as_uri()}?mode=rw",
                uri=True,
                timeout=30.0,
                isolation_level=None,
                check_same_thread=False,
            )
        except BaseException:
            if _provision and provisioned_path:
                _remove_partial_authority(
                    database_path=normalized,
                    anchor_path=authority_anchor,
                )
            raise
        try:
            opened_identity = _database_identity(
                normalized,
                missing_ok=False,
            )
            if (
                initial_identity is not None
                and initial_identity != opened_identity
            ):
                raise BackendRunReplayGuardError(
                    "backend run replay database identity changed while "
                    "opening"
                )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA recursive_triggers = ON")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 30000")
            self._authority_id = self._initialise_schema(
                create=_provision,
            )
            if (
                expected_authority_id is not None
                and self._authority_id != expected_authority_id
            ):
                raise BackendRunReplayGuardError(
                    "backend run replay authority does not match the "
                    "externally pinned authority id"
                )
            probed_authority_id = _probe_authority_id(normalized)
            if probed_authority_id != self._authority_id:
                raise BackendRunReplayGuardError(
                    "backend run replay database identity changed while "
                    "opening"
                )
            if (
                _database_identity(normalized, missing_ok=False)
                != opened_identity
            ):
                raise BackendRunReplayGuardError(
                    "backend run replay database identity changed during "
                    "setup"
                )
            self._database_identity = opened_identity
            if _provision:
                replay_state = _read_replay_state(
                    self._conn,
                    authority_id=self._authority_id,
                )
                _create_authority_anchor(
                    authority_anchor,
                    database_path=normalized,
                    authority_id=self._authority_id,
                    generation=replay_state.generation,
                    state_hash=replay_state.state_hash,
                )
            else:
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    replay_state = _read_replay_state(
                        self._conn,
                        authority_id=self._authority_id,
                    )
                    anchor = _read_authority_anchor(
                        authority_anchor,
                        database_path=normalized,
                    )
                    if anchor.authority_id != self._authority_id:
                        raise BackendRunReplayGuardError(
                            "backend run replay authority anchor mismatch"
                        )
                    self._validate_anchor_state(
                        anchor=anchor,
                        replay_state=replay_state,
                        allow_recovery=True,
                    )
                    self._conn.execute("COMMIT")
                except BaseException:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = FULL")
            _validate_sqlite_sidecar_security(
                normalized,
                repair=_provision,
            )
            self._assert_bound_database_identity()
        except BaseException:
            self._conn.close()
            if _provision and provisioned_path:
                _remove_partial_authority(
                    database_path=normalized,
                    anchor_path=authority_anchor,
                )
            raise

    def __enter__(self) -> "SQLiteBackendRunReplayGuard":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def authority_id(self) -> str:
        return self._authority_id

    def _assert_bound_database_identity(self) -> None:
        if self._poisoned:
            raise BackendRunReplayGuardError(
                "backend run replay guard is poisoned after a committed "
                "store detachment"
            )
        current_identity = _database_identity(
            Path(self.path),
            missing_ok=False,
        )
        if current_identity != self._database_identity:
            raise BackendRunReplayGuardError(
                "backend run replay database identity changed"
            )
        try:
            anchor = _read_authority_anchor(
                Path(self.authority_anchor_path),
                database_path=Path(self.path),
            )
        except BackendRunReplayGuardError as exc:
            raise BackendRunReplayGuardError(
                "backend run replay database identity changed"
            ) from exc
        if anchor.authority_id != self._authority_id:
            raise BackendRunReplayGuardError(
                "backend run replay database identity changed"
            )

    def _validate_anchor_state(
        self,
        *,
        anchor: _AuthorityAnchor,
        replay_state: _ReplayState,
        allow_recovery: bool,
    ) -> None:
        if anchor.authority_id != self._authority_id:
            raise BackendRunReplayGuardError(
                "backend run replay authority anchor mismatch"
            )
        if (
            anchor.generation == replay_state.generation
            and anchor.state_hash == replay_state.state_hash
        ):
            return
        if replay_state.generation < anchor.generation:
            raise BackendRunReplayGuardError(
                "backend run replay state rollback was detected"
            )
        if replay_state.generation == anchor.generation:
            raise BackendRunReplayGuardError(
                "backend run replay state differs from its authority anchor"
            )
        if (
            allow_recovery
            and replay_state.generation == anchor.generation + 1
            and replay_state.previous_state_hash == anchor.state_hash
        ):
            _replace_authority_anchor(
                Path(self.authority_anchor_path),
                database_path=Path(self.path),
                authority_id=self._authority_id,
                generation=replay_state.generation,
                state_hash=replay_state.state_hash,
            )
            return
        raise BackendRunReplayGuardError(
            "backend run replay state is ahead of its authority anchor by an "
            "unrecoverable generation"
        )

    def _sync_authority_anchor_to_database(self) -> None:
        replay_state = _read_replay_state(
            self._conn,
            authority_id=self._authority_id,
        )
        anchor = _read_authority_anchor(
            Path(self.authority_anchor_path),
            database_path=Path(self.path),
        )
        self._validate_anchor_state(
            anchor=anchor,
            replay_state=replay_state,
            allow_recovery=True,
        )

    def _commit_bound_transaction(self) -> None:
        self._assert_bound_database_identity()
        self._conn.execute("COMMIT")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._assert_bound_database_identity()
                self._sync_authority_anchor_to_database()
                _validate_sqlite_sidecar_security(
                    Path(self.path),
                    repair=True,
                )
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
        except BaseException as exc:
            self._poisoned = True
            raise BackendRunReplayCommittedDetachedError(
                "backend run replay transaction committed but its canonical "
                "store locator or authority anchor detached"
            ) from exc

    def _commit_bound_read(self) -> None:
        self._assert_bound_database_identity()
        self._conn.execute("COMMIT")
        self._assert_bound_database_identity()

    def _validate_runtime_authority(self) -> _ReplayState:
        self._validate_schema()
        self._validate_referential_integrity()
        if _read_authority_id(self._conn) != self._authority_id:
            raise BackendRunReplayGuardError(
                "backend run replay authority identity changed"
            )
        replay_state = _read_replay_state(
            self._conn,
            authority_id=self._authority_id,
        )
        anchor = _read_authority_anchor(
            Path(self.authority_anchor_path),
            database_path=Path(self.path),
        )
        self._validate_anchor_state(
            anchor=anchor,
            replay_state=replay_state,
            allow_recovery=True,
        )
        return replay_state

    def _advance_replay_state(
        self,
        previous: _ReplayState,
    ) -> _ReplayState:
        generation = previous.generation + 1
        state_hash = _compute_replay_state_hash(
            self._conn,
            authority_id=self._authority_id,
            generation=generation,
            previous_state_hash=previous.state_hash,
        )
        self._conn.execute(
            """
            UPDATE swe_bench_replay_state
               SET generation=?,
                   previous_state_hash=?,
                   state_hash=?
             WHERE singleton=1
            """,
            (
                generation,
                previous.state_hash,
                state_hash,
            ),
        )
        return _ReplayState(
            generation=generation,
            previous_state_hash=previous.state_hash,
            state_hash=state_hash,
        )

    def consume(
        self,
        *,
        backend_id: str,
        backend_run_id: str,
        authority_hash: str,
    ) -> bool:
        """Return True once, False for an identical replay, and raise on drift."""
        normalized_backend_id = _required_text(
            backend_id,
            field="backend_id",
        )
        normalized_backend_run_id = _required_text(
            backend_run_id,
            field="backend_run_id",
        )
        normalized_authority_hash = _canonical_sha256(authority_hash)
        with self._lock:
            self._assert_bound_database_identity()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                replay_state = self._validate_runtime_authority()
                existing = self._conn.execute(
                    """
                    SELECT authority_hash
                      FROM swe_bench_backend_run_consumptions
                     WHERE backend_id=? AND backend_run_id=?
                    """,
                    (
                        normalized_backend_id,
                        normalized_backend_run_id,
                    ),
                ).fetchone()
                if existing is not None:
                    observed_hash = str(existing["authority_hash"])
                    self._commit_bound_transaction()
                    if observed_hash != normalized_authority_hash:
                        raise BackendRunReplayConflictError(
                            "backend run was already consumed with a "
                            "different authority hash"
                        )
                    return False
                self._conn.execute(
                    """
                    INSERT INTO swe_bench_backend_run_consumptions(
                      schema_version, backend_id, backend_run_id,
                      authority_hash, consumed_at_ms
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        BACKEND_RUN_REPLAY_SCHEMA_VERSION,
                        normalized_backend_id,
                        normalized_backend_run_id,
                        normalized_authority_hash,
                        time.time_ns() // 1_000_000,
                    ),
                )
                self._advance_replay_state(replay_state)
                self._commit_bound_transaction()
                return True
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _validated_verification_attempt_from_row(
        self,
        row: sqlite3.Row,
    ) -> VerificationAttemptRecord:
        record = _verification_attempt_from_row(row)
        if record.state != "COMPLETED":
            return record
        consumed = self._conn.execute(
            """
            SELECT authority_hash
              FROM swe_bench_backend_run_consumptions
             WHERE backend_id=? AND backend_run_id=?
            """,
            (
                record.requested_backend_id,
                record.backend_run_id,
            ),
        ).fetchone()
        if consumed is None:
            raise BackendRunReplayGuardError(
                "completed verification attempt consumption is missing"
            )
        if str(consumed["authority_hash"]) != record.authority_hash:
            raise BackendRunReplayGuardError(
                "completed verification attempt consumption authority "
                "mismatch"
            )
        return record

    def prepare_verification_attempt(
        self,
        spec: VerificationAttemptSpec,
        *,
        backend_id: str,
        backend_manifest_hash: str,
    ) -> VerificationAttemptRecord:
        """Persist one stable nonce before any recoverable backend launch."""
        if not isinstance(spec, VerificationAttemptSpec):
            raise ValueError(
                "verification attempt requires a VerificationAttemptSpec"
            )
        normalized_backend_id = _required_text(
            backend_id,
            field="backend_id",
        )
        normalized_manifest_hash = _canonical_sha256(
            backend_manifest_hash,
            field="backend_manifest_hash",
        )
        with self._lock:
            self._assert_bound_database_identity()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                replay_state = self._validate_runtime_authority()
                existing = self._conn.execute(
                    """
                    SELECT *
                      FROM swe_bench_verification_attempts
                     WHERE slot_key=?
                    """,
                    (spec.effective_slot_key,),
                ).fetchone()
                if existing is not None:
                    record = self._validated_verification_attempt_from_row(
                        existing
                    )
                    if (
                        record.attempt_key != spec.attempt_key
                        or record.requested_backend_id
                        != normalized_backend_id
                        or record.requested_backend_manifest_hash
                        != normalized_manifest_hash
                    ):
                        raise VerificationAttemptConflictError(
                            "verification attempt was prepared with "
                            "different immutable authority"
                        )
                    self._commit_bound_transaction()
                    return record
                request_nonce = secrets.token_hex(32)
                prepared_at_ms = time.time_ns() // 1_000_000
                prepared_hash = _verification_prepared_hash(
                    attempt_key=spec.attempt_key,
                    slot_key=spec.effective_slot_key,
                    request_nonce=request_nonce,
                    requested_backend_id=normalized_backend_id,
                    requested_backend_manifest_hash=(
                        normalized_manifest_hash
                    ),
                    prepared_at_ms=prepared_at_ms,
                )
                self._conn.execute(
                    """
                    INSERT INTO swe_bench_verification_attempts(
                      schema_version, attempt_key, slot_key,
                      execution_spec_hash, frozen_result_hash,
                      model_patch_sha256, producer_run_result_hash,
                      verifier_id, verifier_version, verifier_hash,
                      verification_policy_hash, context_json, context_hash,
                      requested_backend_id,
                      requested_backend_manifest_hash,
                      request_nonce, prepared_hash, state, prepared_at_ms
                    ) VALUES(
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'PREPARED', ?
                    )
                    """,
                    (
                        VERIFICATION_ATTEMPT_SCHEMA_VERSION,
                        spec.attempt_key,
                        spec.effective_slot_key,
                        spec.execution_spec_hash,
                        spec.frozen_result_hash,
                        spec.model_patch_sha256,
                        spec.producer_run_result_hash,
                        spec.verifier_id,
                        spec.verifier_version,
                        spec.verifier_hash,
                        spec.verification_policy_hash,
                        _canonical_json_text(spec.context),
                        spec.context_hash,
                        normalized_backend_id,
                        normalized_manifest_hash,
                        request_nonce,
                        prepared_hash,
                        prepared_at_ms,
                    ),
                )
                row = self._conn.execute(
                    """
                    SELECT *
                      FROM swe_bench_verification_attempts
                     WHERE attempt_key=?
                    """,
                    (spec.attempt_key,),
                ).fetchone()
                if row is None:
                    raise BackendRunReplayGuardError(
                        "verification attempt preparation was not retained"
                    )
                record = self._validated_verification_attempt_from_row(row)
                self._advance_replay_state(replay_state)
                self._commit_bound_transaction()
                return record
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def get_verification_attempt(
        self,
        *,
        attempt_key: str | None = None,
        slot_key: str | None = None,
    ) -> VerificationAttemptRecord | None:
        """Return one validated attempt by exact attempt or owner slot."""
        if (attempt_key is None) == (slot_key is None):
            raise ValueError(
                "provide exactly one of attempt_key or slot_key"
            )
        field_name = "attempt_key" if attempt_key is not None else "slot_key"
        raw_value = attempt_key if attempt_key is not None else slot_key
        normalized = _canonical_sha256(raw_value, field=field_name)
        with self._lock:
            self._assert_bound_database_identity()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._validate_runtime_authority()
                row = self._conn.execute(
                    f"""
                    SELECT *
                      FROM swe_bench_verification_attempts
                     WHERE {field_name}=?
                    """,
                    (normalized,),
                ).fetchone()
                record = (
                    None
                    if row is None
                    else self._validated_verification_attempt_from_row(row)
                )
                self._commit_bound_read()
                return record
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def list_verification_attempts(
        self,
    ) -> tuple[VerificationAttemptRecord, ...]:
        """Return every attempt in deterministic preparation order."""
        with self._lock:
            self._assert_bound_database_identity()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._validate_runtime_authority()
                rows = self._conn.execute(
                    """
                    SELECT *
                      FROM swe_bench_verification_attempts
                     ORDER BY prepared_at_ms, attempt_key
                    """
                ).fetchall()
                records = tuple(
                    self._validated_verification_attempt_from_row(row)
                    for row in rows
                )
                self._commit_bound_read()
                return records
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def complete_verification_attempt(
        self,
        *,
        attempt_key: str,
        backend_id: str,
        backend_run_id: str,
        authority_hash: str,
        grade: Mapping[str, Any],
    ) -> VerificationAttemptRecord:
        """Atomically consume one backend run and retain its canonical grade."""
        normalized_attempt_key = _canonical_sha256(
            attempt_key,
            field="attempt_key",
        )
        normalized_backend_id = _required_text(
            backend_id,
            field="backend_id",
        )
        normalized_backend_run_id = _required_text(
            backend_run_id,
            field="backend_run_id",
        )
        normalized_authority_hash = _canonical_sha256(
            authority_hash,
            field="authority_hash",
        )
        canonical_grade = _canonical_json_mapping(grade, field="grade")
        grade_json = _canonical_json_text(canonical_grade)
        grade_hash = _sha256_json(canonical_grade)
        with self._lock:
            self._assert_bound_database_identity()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                replay_state = self._validate_runtime_authority()
                row = self._conn.execute(
                    """
                    SELECT *
                      FROM swe_bench_verification_attempts
                     WHERE attempt_key=?
                    """,
                    (normalized_attempt_key,),
                ).fetchone()
                if row is None:
                    raise VerificationAttemptConflictError(
                        "verification attempt was not prepared"
                    )
                existing = self._validated_verification_attempt_from_row(
                    row
                )
                completion_hash = _verification_completion_hash(
                    attempt_key=normalized_attempt_key,
                    prepared_hash=existing.prepared_hash,
                    request_nonce=existing.request_nonce,
                    backend_id=normalized_backend_id,
                    backend_run_id=normalized_backend_run_id,
                    authority_hash=normalized_authority_hash,
                    grade_hash=grade_hash,
                )
                if existing.state == "COMPLETED":
                    if (
                        existing.requested_backend_id
                        != normalized_backend_id
                        or existing.backend_run_id
                        != normalized_backend_run_id
                        or existing.authority_hash
                        != normalized_authority_hash
                        or existing.grade_hash != grade_hash
                        or existing.completion_hash != completion_hash
                        or _canonical_json_text(existing.grade or {})
                        != grade_json
                    ):
                        raise VerificationAttemptConflictError(
                            "verification attempt was completed with "
                            "different authority"
                        )
                    self._commit_bound_transaction()
                    return existing
                if (
                    existing.requested_backend_id
                    != normalized_backend_id
                ):
                    raise VerificationAttemptConflictError(
                        "verification completion backend differs from "
                        "prepared authority"
                    )
                consumed = self._conn.execute(
                    """
                    SELECT authority_hash
                      FROM swe_bench_backend_run_consumptions
                     WHERE backend_id=? AND backend_run_id=?
                    """,
                    (
                        normalized_backend_id,
                        normalized_backend_run_id,
                    ),
                ).fetchone()
                if consumed is not None:
                    raise VerificationAttemptConflictError(
                        "backend run was consumed outside this "
                        "verification attempt"
                    )
                self._conn.execute(
                    """
                    INSERT INTO swe_bench_backend_run_consumptions(
                      schema_version, backend_id, backend_run_id,
                      authority_hash, consumed_at_ms
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        BACKEND_RUN_REPLAY_SCHEMA_VERSION,
                        normalized_backend_id,
                        normalized_backend_run_id,
                        normalized_authority_hash,
                        time.time_ns() // 1_000_000,
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE swe_bench_verification_attempts
                       SET state='COMPLETED',
                           completed_at_ms=?,
                           backend_run_id=?,
                           authority_hash=?,
                           grade_json=?,
                           grade_hash=?,
                           completion_hash=?
                     WHERE attempt_key=? AND state='PREPARED'
                    """,
                    (
                        max(
                            time.time_ns() // 1_000_000,
                            existing.prepared_at_ms,
                        ),
                        normalized_backend_run_id,
                        normalized_authority_hash,
                        grade_json,
                        grade_hash,
                        completion_hash,
                        normalized_attempt_key,
                    ),
                )
                completed_row = self._conn.execute(
                    """
                    SELECT *
                      FROM swe_bench_verification_attempts
                     WHERE attempt_key=?
                    """,
                    (normalized_attempt_key,),
                ).fetchone()
                if completed_row is None:
                    raise BackendRunReplayGuardError(
                        "verification attempt completion was not retained"
                    )
                completed = self._validated_verification_attempt_from_row(
                    completed_row
                )
                if completed.state != "COMPLETED":
                    raise BackendRunReplayGuardError(
                        "verification attempt did not reach completion"
                    )
                self._advance_replay_state(replay_state)
                self._commit_bound_transaction()
                return completed
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _initialise_schema(self, *, create: bool) -> str:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if create:
                    self._conn.execute(_CANONICAL_AUTHORITY_TABLE_SQL)
                    for trigger_sql in (
                        _CANONICAL_AUTHORITY_TRIGGER_SQL
                    ):
                        self._conn.execute(trigger_sql)
                    self._conn.execute(_CANONICAL_STATE_TABLE_SQL)
                    for trigger_sql in _CANONICAL_STATE_TRIGGER_SQL:
                        self._conn.execute(trigger_sql)
                    self._conn.execute(_CANONICAL_REPLAY_TABLE_SQL)
                    for trigger_sql in _CANONICAL_REPLAY_TRIGGER_SQL:
                        self._conn.execute(trigger_sql)
                    self._conn.execute(
                        _CANONICAL_VERIFICATION_ATTEMPT_TABLE_SQL
                    )
                    for trigger_sql in (
                        _CANONICAL_VERIFICATION_ATTEMPT_TRIGGER_SQL
                    ):
                        self._conn.execute(trigger_sql)
                    authority_id = secrets.token_hex(32)
                    self._conn.execute(
                        """
                        INSERT INTO swe_bench_replay_authority(
                          schema_version, singleton, authority_id,
                          created_at_ms
                        ) VALUES(?, 1, ?, ?)
                        """,
                        (
                            REPLAY_AUTHORITY_SCHEMA_VERSION,
                            authority_id,
                            time.time_ns() // 1_000_000,
                        ),
                    )
                    initial_state_hash = _compute_replay_state_hash(
                        self._conn,
                        authority_id=authority_id,
                        generation=0,
                        previous_state_hash=None,
                    )
                    self._conn.execute(
                        """
                        INSERT INTO swe_bench_replay_state(
                          schema_version, singleton, generation,
                          previous_state_hash, state_hash
                        ) VALUES(?, 1, 0, NULL, ?)
                        """,
                        (
                            REPLAY_STATE_SCHEMA_VERSION,
                            initial_state_hash,
                        ),
                    )
                self._validate_schema()
                self._validate_database_integrity()
                authority_id = _read_authority_id(self._conn)
                _read_replay_state(
                    self._conn,
                    authority_id=authority_id,
                )
                self._conn.execute("COMMIT")
                return authority_id
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _validate_schema(self) -> None:
        rows = self._conn.execute(
            """
            SELECT type, name, tbl_name, sql
              FROM sqlite_master
             WHERE (
                   name IN (?, ?, ?, ?)
                OR tbl_name IN (?, ?, ?, ?)
             )
               AND sql IS NOT NULL
             ORDER BY type, name
            """,
            (
                _AUTHORITY_TABLE_NAME,
                _STATE_TABLE_NAME,
                _REPLAY_TABLE_NAME,
                _VERIFICATION_ATTEMPT_TABLE_NAME,
                _AUTHORITY_TABLE_NAME,
                _STATE_TABLE_NAME,
                _REPLAY_TABLE_NAME,
                _VERIFICATION_ATTEMPT_TABLE_NAME,
            ),
        ).fetchall()
        expected = {
            ("table", _AUTHORITY_TABLE_NAME): (
                _AUTHORITY_TABLE_NAME,
                _normalise_sqlite_ddl(
                    _CANONICAL_AUTHORITY_TABLE_SQL
                ),
            ),
            ("table", _REPLAY_TABLE_NAME): (
                _REPLAY_TABLE_NAME,
                _normalise_sqlite_ddl(_CANONICAL_REPLAY_TABLE_SQL),
            ),
            ("table", _STATE_TABLE_NAME): (
                _STATE_TABLE_NAME,
                _normalise_sqlite_ddl(_CANONICAL_STATE_TABLE_SQL),
            ),
            ("table", _VERIFICATION_ATTEMPT_TABLE_NAME): (
                _VERIFICATION_ATTEMPT_TABLE_NAME,
                _normalise_sqlite_ddl(
                    _CANONICAL_VERIFICATION_ATTEMPT_TABLE_SQL
                ),
            ),
            **{
                ("trigger", _sqlite_schema_object_name(trigger_sql)): (
                    _STATE_TABLE_NAME,
                    _normalise_sqlite_ddl(trigger_sql),
                )
                for trigger_sql in _CANONICAL_STATE_TRIGGER_SQL
            },
            **{
                ("trigger", _sqlite_schema_object_name(trigger_sql)): (
                    _REPLAY_TABLE_NAME,
                    _normalise_sqlite_ddl(trigger_sql),
                )
                for trigger_sql in _CANONICAL_REPLAY_TRIGGER_SQL
            },
            **{
                ("trigger", _sqlite_schema_object_name(trigger_sql)): (
                    _VERIFICATION_ATTEMPT_TABLE_NAME,
                    _normalise_sqlite_ddl(trigger_sql),
                )
                for trigger_sql in (
                    _CANONICAL_VERIFICATION_ATTEMPT_TRIGGER_SQL
                )
            },
            **{
                ("trigger", _sqlite_schema_object_name(trigger_sql)): (
                    _AUTHORITY_TABLE_NAME,
                    _normalise_sqlite_ddl(trigger_sql),
                )
                for trigger_sql in _CANONICAL_AUTHORITY_TRIGGER_SQL
            },
        }
        observed = {
            (str(row["type"]), str(row["name"])): (
                str(row["tbl_name"]),
                _normalise_sqlite_ddl(row["sql"]),
            )
            for row in rows
        }
        if observed != expected:
            raise BackendRunReplayGuardError(
                "backend run replay schema definition mismatch"
            )

    def _validate_referential_integrity(self) -> None:
        violations = self._conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if violations:
            raise BackendRunReplayGuardError(
                "backend run replay foreign key integrity check failed"
            )

    def _validate_database_integrity(self) -> None:
        integrity_rows = self._conn.execute(
            "PRAGMA integrity_check"
        ).fetchall()
        if [str(row[0]) for row in integrity_rows] != ["ok"]:
            raise BackendRunReplayGuardError(
                "backend run replay database integrity check failed"
            )
        self._validate_referential_integrity()


def _read_authority_id(connection: sqlite3.Connection) -> str:
    try:
        rows = connection.execute(
            """
            SELECT schema_version, singleton, authority_id
              FROM swe_bench_replay_authority
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise BackendRunReplayGuardError(
            "backend run replay authority record is unavailable"
        ) from exc
    if len(rows) != 1:
        raise BackendRunReplayGuardError(
            "backend run replay authority record is not singular"
        )
    row = rows[0]
    if (
        str(row["schema_version"]) != REPLAY_AUTHORITY_SCHEMA_VERSION
        or int(row["singleton"]) != 1
    ):
        raise BackendRunReplayGuardError(
            "backend run replay authority record is invalid"
        )
    return _canonical_sha256(
        row["authority_id"],
        field="authority_id",
    )


def _canonical_journal_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    order_by: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"SELECT * FROM {table} ORDER BY {order_by}"
    ).fetchall()
    return [
        {
            str(key): row[key]
            for key in row.keys()
        }
        for row in rows
    ]


def _compute_replay_state_hash(
    connection: sqlite3.Connection,
    *,
    authority_id: str,
    generation: int,
    previous_state_hash: str | None,
) -> str:
    return _sha256_json({
        "schema_version": REPLAY_STATE_SCHEMA_VERSION,
        "authority_id": _canonical_sha256(
            authority_id,
            field="authority_id",
        ),
        "generation": generation,
        "previous_state_hash": previous_state_hash,
        "backend_run_consumptions": _canonical_journal_rows(
            connection,
            table=_REPLAY_TABLE_NAME,
            order_by="backend_id, backend_run_id",
        ),
        "verification_attempts": _canonical_journal_rows(
            connection,
            table=_VERIFICATION_ATTEMPT_TABLE_NAME,
            order_by="attempt_key",
        ),
    })


def _read_replay_state(
    connection: sqlite3.Connection,
    *,
    authority_id: str,
) -> _ReplayState:
    try:
        rows = connection.execute(
            """
            SELECT schema_version, singleton, generation,
                   previous_state_hash, state_hash
              FROM swe_bench_replay_state
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise BackendRunReplayGuardError(
            "backend run replay state record is unavailable"
        ) from exc
    if len(rows) != 1:
        raise BackendRunReplayGuardError(
            "backend run replay state record is not singular"
        )
    row = rows[0]
    try:
        generation = int(row["generation"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise BackendRunReplayGuardError(
            "backend run replay state generation is invalid"
        ) from exc
    previous_state_hash = (
        _canonical_sha256(
            row["previous_state_hash"],
            field="previous_state_hash",
        )
        if row["previous_state_hash"] is not None
        else None
    )
    state_hash = _canonical_sha256(
        row["state_hash"],
        field="state_hash",
    )
    if (
        str(row["schema_version"]) != REPLAY_STATE_SCHEMA_VERSION
        or int(row["singleton"]) != 1
        or generation < 0
        or (generation == 0) != (previous_state_hash is None)
    ):
        raise BackendRunReplayGuardError(
            "backend run replay state record is invalid"
        )
    expected = _compute_replay_state_hash(
        connection,
        authority_id=authority_id,
        generation=generation,
        previous_state_hash=previous_state_hash,
    )
    if state_hash != expected:
        raise BackendRunReplayGuardError(
            "backend run replay state hash is invalid"
        )
    return _ReplayState(
        generation=generation,
        previous_state_hash=previous_state_hash,
        state_hash=state_hash,
    )


def _probe_authority_id(path: Path) -> str:
    uri = f"{path.as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=30.0,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            return _read_authority_id(connection)
        finally:
            connection.close()
    except (BackendRunReplayGuardError, sqlite3.Error) as exc:
        raise BackendRunReplayGuardError(
            "backend run replay canonical path is not bound to the opened "
            "authority"
        ) from exc


def _authority_anchor_path(database_path: Path) -> Path:
    return database_path.with_name(
        f".{database_path.name}.authority"
    )


def _authority_anchor_exists(path: Path) -> bool:
    _reject_symlink_components(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise BackendRunReplayGuardError(
            "backend run replay authority anchor must be a single-link "
            "regular file"
        )
    return True


def _authority_anchor_payload(
    *,
    database_path: Path,
    authority_id: str,
    generation: int,
    state_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": REPLAY_AUTHORITY_ANCHOR_SCHEMA_VERSION,
        "database_path": str(database_path),
        "authority_id": _canonical_sha256(
            authority_id,
            field="authority_id",
        ),
        "generation": generation,
        "state_hash": _canonical_sha256(
            state_hash,
            field="state_hash",
        ),
    }


def _encoded_authority_anchor(
    *,
    database_path: Path,
    authority_id: str,
    generation: int,
    state_hash: str,
) -> bytes:
    payload = _authority_anchor_payload(
        database_path=database_path,
        authority_id=authority_id,
        generation=generation,
        state_hash=state_hash,
    )
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_private_file(
    path: Path,
    encoded: bytes,
    *,
    replace: bool,
) -> None:
    _reject_symlink_components(path)
    target = (
        path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        if replace
        else path
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(target, flags, 0o600)
        created = True
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(target, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                target.unlink()
            except OSError:
                pass
        raise


def _create_authority_anchor(
    path: Path,
    *,
    database_path: Path,
    authority_id: str,
    generation: int,
    state_hash: str,
) -> None:
    encoded = _encoded_authority_anchor(
        database_path=database_path,
        authority_id=authority_id,
        generation=generation,
        state_hash=state_hash,
    )
    try:
        _write_private_file(path, encoded, replace=False)
    except FileExistsError as exc:
        raise BackendRunReplayGuardError(
            "backend run replay authority anchor already exists"
        ) from exc


def _replace_authority_anchor(
    path: Path,
    *,
    database_path: Path,
    authority_id: str,
    generation: int,
    state_hash: str,
) -> None:
    encoded = _encoded_authority_anchor(
        database_path=database_path,
        authority_id=authority_id,
        generation=generation,
        state_hash=state_hash,
    )
    _write_private_file(path, encoded, replace=True)


def _read_authority_anchor(
    path: Path,
    *,
    database_path: Path,
) -> _AuthorityAnchor:
    _reject_symlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    encoded: bytes | None = None
    for attempt in range(_AUTHORITY_ANCHOR_READ_ATTEMPTS):
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise BackendRunReplayGuardError(
                "backend run replay authority anchor is missing"
            ) from None
        except OSError as exc:
            raise BackendRunReplayGuardError(
                "backend run replay authority anchor cannot be read"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor must be a "
                    "single-link regular file"
                )
            if metadata.st_nlink == 0:
                if attempt + 1 < _AUTHORITY_ANCHOR_READ_ATTEMPTS:
                    continue
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor changed while "
                    "reading"
                )
            if metadata.st_nlink != 1:
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor must be a "
                    "single-link regular file"
                )
            if (
                hasattr(os, "geteuid")
                and metadata.st_uid != os.geteuid()
            ):
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor has a different "
                    "owner"
                )
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor requires private "
                    "permissions"
                )
            if metadata.st_size > _MAX_AUTHORITY_ANCHOR_BYTES:
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor is oversized"
                )
            chunks: list[bytes] = []
            remaining = _MAX_AUTHORITY_ANCHOR_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            candidate = b"".join(chunks)
            if len(candidate) > _MAX_AUTHORITY_ANCHOR_BYTES:
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor is oversized"
                )
            final_metadata = os.fstat(descriptor)
            try:
                path_metadata = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                if attempt + 1 < _AUTHORITY_ANCHOR_READ_ATTEMPTS:
                    continue
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor changed while "
                    "reading"
                ) from None
            if (
                final_metadata.st_nlink == 0
                or path_metadata.st_dev != metadata.st_dev
                or path_metadata.st_ino != metadata.st_ino
            ):
                if attempt + 1 < _AUTHORITY_ANCHOR_READ_ATTEMPTS:
                    continue
                raise BackendRunReplayGuardError(
                    "backend run replay authority anchor changed while "
                    "reading"
                )
            encoded = candidate
            break
        finally:
            os.close(descriptor)
    if encoded is None:
        raise BackendRunReplayGuardError(
            "backend run replay authority anchor changed while reading"
        )
    try:
        payload = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendRunReplayGuardError(
            "backend run replay authority anchor is invalid"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {
            "schema_version",
            "database_path",
            "authority_id",
            "generation",
            "state_hash",
        }
        or payload.get("schema_version")
        != REPLAY_AUTHORITY_ANCHOR_SCHEMA_VERSION
        or payload.get("database_path") != str(database_path)
    ):
        raise BackendRunReplayGuardError(
            "backend run replay authority anchor is invalid"
        )
    generation = payload.get("generation")
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
    ):
        raise BackendRunReplayGuardError(
            "backend run replay authority anchor generation is invalid"
        )
    try:
        return _AuthorityAnchor(
            authority_id=_canonical_sha256(
                payload.get("authority_id"),
                field="authority_id",
            ),
            generation=generation,
            state_hash=_canonical_sha256(
                payload.get("state_hash"),
                field="state_hash",
            ),
        )
    except ValueError as exc:
        raise BackendRunReplayGuardError(
            "backend run replay authority anchor is invalid"
        ) from exc


def _required_text(value: object, *, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} must be non-empty")
    return normalized


def _canonical_sha256(
    value: object,
    *,
    field: str = "authority_hash",
) -> str:
    normalized = str(value).strip()
    if (
        len(normalized) != 64
        or normalized != normalized.casefold()
        or any(character not in "0123456789abcdef" for character in normalized)
    ):
        raise ValueError(
            f"{field} must be a canonical sha256 digest"
        )
    return normalized


def _canonical_json_mapping(
    value: object,
    *,
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    normalized = _normalize_json_value(value, field=field)
    if not isinstance(normalized, dict):
        raise ValueError(f"{field} must be a mapping")
    return normalized


def _normalize_json_value(value: object, *, field: str) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field} must use string keys")
            normalized[key] = _normalize_json_value(
                child,
                field=f"{field}.{key}",
            )
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _normalize_json_value(
                child,
                field=f"{field}[{index}]",
            )
            for index, child in enumerate(value)
        ]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain canonical JSON values")


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            key: _freeze_json_value(child)
            for key, child in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(child) for child in value)
    return value


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _thaw_json_value(child)
            for key, child in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json_value(child) for child in value]
    return value


def _canonical_json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(
        _thaw_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_json(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json_text(value).encode("utf-8")
    ).hexdigest()


def _verification_prepared_hash(
    *,
    attempt_key: str,
    slot_key: str,
    request_nonce: str,
    requested_backend_id: str,
    requested_backend_manifest_hash: str,
    prepared_at_ms: int,
) -> str:
    return _sha256_json({
        "schema_version": (
            "supervisor-swe-bench-verification-preparation/v1"
        ),
        "attempt_key": attempt_key,
        "slot_key": slot_key,
        "request_nonce": request_nonce,
        "requested_backend_id": requested_backend_id,
        "requested_backend_manifest_hash": (
            requested_backend_manifest_hash
        ),
        "prepared_at_ms": prepared_at_ms,
    })


def _verification_completion_hash(
    *,
    attempt_key: str,
    prepared_hash: str,
    request_nonce: str,
    backend_id: str,
    backend_run_id: str,
    authority_hash: str,
    grade_hash: str,
) -> str:
    return _sha256_json({
        "schema_version": (
            "supervisor-swe-bench-verification-completion/v1"
        ),
        "attempt_key": attempt_key,
        "prepared_hash": prepared_hash,
        "request_nonce": request_nonce,
        "backend_id": backend_id,
        "backend_run_id": backend_run_id,
        "authority_hash": authority_hash,
        "grade_hash": grade_hash,
    })


def _verification_attempt_from_row(
    row: sqlite3.Row,
) -> VerificationAttemptRecord:
    try:
        context = json.loads(str(row["context_json"]))
    except json.JSONDecodeError as exc:
        raise BackendRunReplayGuardError(
            "verification attempt context is invalid"
        ) from exc
    spec = VerificationAttemptSpec(
        execution_spec_hash=str(row["execution_spec_hash"]),
        frozen_result_hash=str(row["frozen_result_hash"]),
        model_patch_sha256=str(row["model_patch_sha256"]),
        producer_run_result_hash=str(
            row["producer_run_result_hash"]
        ),
        verifier_id=str(row["verifier_id"]),
        verifier_version=str(row["verifier_version"]),
        verifier_hash=str(row["verifier_hash"]),
        verification_policy_hash=str(
            row["verification_policy_hash"]
        ),
        slot_key=str(row["slot_key"]),
        context=context,
    )
    if (
        str(row["schema_version"])
        != VERIFICATION_ATTEMPT_SCHEMA_VERSION
        or str(row["attempt_key"]) != spec.attempt_key
        or str(row["context_hash"]) != spec.context_hash
        or str(row["context_json"]) != _canonical_json_text(spec.context)
    ):
        raise BackendRunReplayGuardError(
            "verification attempt identity is invalid"
        )
    grade: Mapping[str, Any] | None = None
    raw_grade = row["grade_json"]
    if raw_grade is not None:
        try:
            parsed_grade = json.loads(str(raw_grade))
        except json.JSONDecodeError as exc:
            raise BackendRunReplayGuardError(
                "verification attempt grade is invalid"
            ) from exc
        grade = _canonical_json_mapping(parsed_grade, field="grade")
        if (
            str(raw_grade) != _canonical_json_text(grade)
            or str(row["grade_hash"]) != _sha256_json(grade)
        ):
            raise BackendRunReplayGuardError(
                "verification attempt grade hash is invalid"
            )
    record = VerificationAttemptRecord(
        spec=spec,
        attempt_key=str(row["attempt_key"]),
        slot_key=str(row["slot_key"]),
        state=str(row["state"]),
        request_nonce=str(row["request_nonce"]),
        requested_backend_id=str(row["requested_backend_id"]),
        requested_backend_manifest_hash=str(
            row["requested_backend_manifest_hash"]
        ),
        prepared_at_ms=int(row["prepared_at_ms"]),
        prepared_hash=str(row["prepared_hash"]),
        completed_at_ms=(
            int(row["completed_at_ms"])
            if row["completed_at_ms"] is not None
            else None
        ),
        backend_run_id=(
            str(row["backend_run_id"])
            if row["backend_run_id"] is not None
            else None
        ),
        authority_hash=(
            str(row["authority_hash"])
            if row["authority_hash"] is not None
            else None
        ),
        grade=grade,
        grade_hash=(
            str(row["grade_hash"])
            if row["grade_hash"] is not None
            else None
        ),
        completion_hash=(
            str(row["completion_hash"])
            if row["completion_hash"] is not None
            else None
        ),
    )
    expected_prepared_hash = _verification_prepared_hash(
        attempt_key=record.attempt_key,
        slot_key=record.slot_key,
        request_nonce=record.request_nonce,
        requested_backend_id=record.requested_backend_id,
        requested_backend_manifest_hash=(
            record.requested_backend_manifest_hash
        ),
        prepared_at_ms=record.prepared_at_ms,
    )
    if record.prepared_hash != expected_prepared_hash:
        raise BackendRunReplayGuardError(
            "verification attempt preparation hash is invalid"
        )
    if record.state == "PREPARED":
        if any(
            value is not None
            for value in (
                record.completed_at_ms,
                record.backend_run_id,
                record.authority_hash,
                record.grade,
                record.grade_hash,
                record.completion_hash,
            )
        ):
            raise BackendRunReplayGuardError(
                "prepared verification attempt carries completion data"
            )
        return record
    if (
        record.completed_at_ms is None
        or record.backend_run_id is None
        or record.authority_hash is None
        or record.grade is None
        or record.grade_hash is None
        or record.completion_hash is None
    ):
        raise BackendRunReplayGuardError(
            "completed verification attempt is incomplete"
        )
    _canonical_sha256(record.authority_hash, field="authority_hash")
    expected_completion_hash = _verification_completion_hash(
        attempt_key=record.attempt_key,
        prepared_hash=record.prepared_hash,
        request_nonce=record.request_nonce,
        backend_id=record.requested_backend_id,
        backend_run_id=record.backend_run_id,
        authority_hash=record.authority_hash,
        grade_hash=record.grade_hash,
    )
    if record.completion_hash != expected_completion_hash:
        raise BackendRunReplayGuardError(
            "verification attempt completion hash is invalid"
        )
    return record


def _normalise_sqlite_ddl(value: object) -> str:
    return " ".join(str(value or "").strip().rstrip(";").split())


def _sqlite_schema_object_name(sql: str) -> str:
    return sql.split()[2]


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(
                "durable backend run replay guard path contains a symlink "
                f"component: {current}"
            )
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(
                "durable backend run replay guard path contains a "
                f"non-directory component: {current}"
            )


def _database_identity(
    path: Path,
    *,
    missing_ok: bool,
) -> tuple[int, int] | None:
    _reject_symlink_components(path)
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise BackendRunReplayGuardError(
            "backend run replay database is missing"
        ) from None
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            "durable backend run replay guard database must be a regular file"
        )
    if metadata.st_nlink != 1:
        raise ValueError(
            "durable backend run replay guard database must have one hard link"
        )
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise BackendRunReplayGuardError(
            "backend run replay database has a different owner"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BackendRunReplayGuardError(
            "backend run replay database requires private permissions"
        )
    return metadata.st_dev, metadata.st_ino


def _sqlite_sidecar_paths(path: Path) -> tuple[Path, ...]:
    return tuple(
        Path(str(path) + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    )


def _validate_sqlite_sidecar_security(
    path: Path,
    *,
    repair: bool,
) -> None:
    for sidecar in _sqlite_sidecar_paths(path):
        _reject_symlink_components(sidecar)
        try:
            metadata = sidecar.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise BackendRunReplayGuardError(
                "backend run replay sidecar must be a single-link regular file"
            )
        if (
            hasattr(os, "geteuid")
            and metadata.st_uid != os.geteuid()
        ):
            raise BackendRunReplayGuardError(
                "backend run replay sidecar has a different owner"
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            if repair:
                os.chmod(sidecar, 0o600, follow_symlinks=False)
            else:
                raise BackendRunReplayGuardError(
                    "backend run replay sidecar requires private permissions"
                )


def _create_private_database_file(path: Path) -> None:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_descriptor = os.open(path.parent, directory_flags)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _remove_partial_authority(
    *,
    database_path: Path,
    anchor_path: Path,
) -> None:
    for candidate in (
        *_sqlite_sidecar_paths(database_path),
        database_path,
        anchor_path,
    ):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue


__all__ = [
    "BACKEND_RUN_REPLAY_SCHEMA_VERSION",
    "VERIFICATION_ATTEMPT_KEY_SCHEMA_VERSION",
    "VERIFICATION_ATTEMPT_SCHEMA_VERSION",
    "BackendRunReplayConflictError",
    "BackendRunReplayGuardError",
    "SQLiteBackendRunReplayGuard",
    "VerificationAttemptConflictError",
    "VerificationAttemptRecord",
    "VerificationAttemptSpec",
]
