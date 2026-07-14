"""Immutable grade revisions and decision-citation validation.

The module owns grade history independently of the supervisor's mutable state
store. SHA-256 values here are content-integrity identifiers, not signatures.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .task_environment import FrozenTaskResult, Grade
from .trace_graph import (
    EdgeType,
    NodeType,
    TraceEdge,
    TraceGraph,
    TraceIdentity,
    TraceNode,
    trace_instance_id_from_hash,
)


GRADE_REVISION_SCHEMA_VERSION = "supervisor-grade-revision/v1"
GRADE_INVALIDATION_SCHEMA_VERSION = "supervisor-grade-invalidation/v1"
GRADE_TERMINAL_COMMIT_SCHEMA_VERSION = (
    "supervisor-grade-terminal-commit/v1"
)
DECISION_GRADE_VALIDATION_SCHEMA_VERSION = (
    "supervisor-decision-grade-validation/v1"
)
GRADE_DECISION_SCHEMA_VERSION = "supervisor-grade-backed-decision/v1"
DEFAULT_GRADE_TRACE_NAMESPACE = "harness-v1/gradebook"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class GradeBookError(RuntimeError):
    """Base error for the immutable grade ledger."""


class GradeValidationError(GradeBookError, ValueError):
    """A grade or immutable reference is incomplete or inconsistent."""


class GradeNotFoundError(GradeBookError, LookupError):
    """A requested grade revision does not exist."""


class SupersessionConflict(GradeBookError):
    """A requested append would create a second root or a history branch."""


class GradeIntegrityError(GradeBookError):
    """Persisted content no longer matches its recorded revision hash."""


@dataclass(frozen=True)
class RunEnvelopeRef:
    """Hash-pinned identity of one captured run result."""

    run_id: str
    run_envelope_hash: str
    frozen_result_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_text("run_id", self.run_id))
        object.__setattr__(
            self,
            "run_envelope_hash",
            _require_sha256("run_envelope_hash", self.run_envelope_hash),
        )
        object.__setattr__(
            self,
            "frozen_result_hash",
            _require_sha256("frozen_result_hash", self.frozen_result_hash),
        )

    @classmethod
    def from_frozen_result(
        cls,
        *,
        run_id: str,
        run_envelope_hash: str,
        frozen_result: FrozenTaskResult,
    ) -> "RunEnvelopeRef":
        return cls(
            run_id=run_id,
            run_envelope_hash=run_envelope_hash,
            frozen_result_hash=frozen_result.result_hash,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "run_envelope_hash": self.run_envelope_hash,
            "frozen_result_hash": self.frozen_result_hash,
        }


@dataclass(frozen=True)
class GradeRevision:
    grade_id: str
    revision_hash: str
    revision_number: int
    run_envelope: RunEnvelopeRef
    verifier_id: str
    verifier_version: str
    verifier_config_hash: str
    verifier_implementation_hash: str
    passed: bool
    score: float
    evidence: Mapping[str, Any]
    failure_classification: str
    flake_classification: str
    supersedes_grade_id: str | None
    recorded_at_ms: int
    schema_version: str = GRADE_REVISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "grade_id", _require_text("grade_id", self.grade_id))
        object.__setattr__(
            self,
            "revision_hash",
            _require_sha256("revision_hash", self.revision_hash),
        )
        if self.revision_number < 1:
            raise GradeValidationError("revision_number must be positive")
        object.__setattr__(
            self,
            "verifier_id",
            _require_text("verifier_id", self.verifier_id),
        )
        object.__setattr__(
            self,
            "verifier_version",
            _require_text("verifier_version", self.verifier_version),
        )
        object.__setattr__(
            self,
            "verifier_config_hash",
            _require_sha256(
                "verifier_config_hash",
                self.verifier_config_hash,
            ),
        )
        object.__setattr__(
            self,
            "verifier_implementation_hash",
            _require_sha256(
                "verifier_implementation_hash",
                self.verifier_implementation_hash,
            ),
        )
        object.__setattr__(self, "score", _require_score(self.score))
        object.__setattr__(
            self,
            "evidence",
            _freeze_json(_normalise_json(self.evidence, field="evidence")),
        )
        object.__setattr__(
            self,
            "failure_classification",
            str(self.failure_classification),
        )
        object.__setattr__(
            self,
            "flake_classification",
            str(self.flake_classification),
        )
        if self.supersedes_grade_id is not None:
            object.__setattr__(
                self,
                "supersedes_grade_id",
                _require_text(
                    "supersedes_grade_id",
                    self.supersedes_grade_id,
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            **_grade_revision_payload(
                grade_id=self.grade_id,
                revision_number=self.revision_number,
                run_envelope=self.run_envelope,
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                verifier_config_hash=self.verifier_config_hash,
                verifier_implementation_hash=self.verifier_implementation_hash,
                passed=self.passed,
                score=self.score,
                evidence=self.evidence,
                failure_classification=self.failure_classification,
                flake_classification=self.flake_classification,
                supersedes_grade_id=self.supersedes_grade_id,
                recorded_at_ms=self.recorded_at_ms,
            ),
            "revision_hash": self.revision_hash,
        }


@dataclass(frozen=True)
class GradeInvalidation:
    invalidation_id: str
    invalidation_hash: str
    grade_id: str
    grade_revision_hash: str
    kind: str
    reason: str
    replacement_grade_id: str | None
    replacement_revision_hash: str | None
    recorded_at_ms: int
    schema_version: str = GRADE_INVALIDATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "invalidation_id",
            _require_text("invalidation_id", self.invalidation_id),
        )
        object.__setattr__(
            self,
            "invalidation_hash",
            _require_sha256("invalidation_hash", self.invalidation_hash),
        )
        object.__setattr__(self, "grade_id", _require_text("grade_id", self.grade_id))
        object.__setattr__(
            self,
            "grade_revision_hash",
            _require_sha256("grade_revision_hash", self.grade_revision_hash),
        )
        object.__setattr__(self, "kind", _require_text("kind", self.kind))
        object.__setattr__(self, "reason", _require_text("reason", self.reason))
        replacement_fields = (
            self.replacement_grade_id,
            self.replacement_revision_hash,
        )
        if any(value is not None for value in replacement_fields):
            if not all(value is not None for value in replacement_fields):
                raise GradeValidationError(
                    "replacement grade id and revision hash must be recorded together"
                )
            object.__setattr__(
                self,
                "replacement_grade_id",
                _require_text(
                    "replacement_grade_id",
                    self.replacement_grade_id,
                ),
            )
            object.__setattr__(
                self,
                "replacement_revision_hash",
                _require_sha256(
                    "replacement_revision_hash",
                    self.replacement_revision_hash,
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            **_grade_invalidation_payload(
                invalidation_id=self.invalidation_id,
                grade_id=self.grade_id,
                grade_revision_hash=self.grade_revision_hash,
                kind=self.kind,
                reason=self.reason,
                replacement_grade_id=self.replacement_grade_id,
                replacement_revision_hash=self.replacement_revision_hash,
                recorded_at_ms=self.recorded_at_ms,
            ),
            "invalidation_hash": self.invalidation_hash,
        }


@dataclass(frozen=True)
class GradeTerminalCommit:
    commit_id: str
    commit_hash: str
    grade_id: str
    grade_revision_hash: str
    experiment_id: str
    task_id: str
    arm: str
    terminal_state: str
    terminal_state_hash: str
    recorded_at_ms: int
    schema_version: str = GRADE_TERMINAL_COMMIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "commit_id",
            _require_text("commit_id", self.commit_id),
        )
        object.__setattr__(
            self,
            "commit_hash",
            _require_sha256("commit_hash", self.commit_hash),
        )
        object.__setattr__(
            self,
            "grade_id",
            _require_text("grade_id", self.grade_id),
        )
        object.__setattr__(
            self,
            "grade_revision_hash",
            _require_sha256(
                "grade_revision_hash",
                self.grade_revision_hash,
            ),
        )
        object.__setattr__(
            self,
            "experiment_id",
            _require_text("experiment_id", self.experiment_id),
        )
        object.__setattr__(
            self,
            "task_id",
            _require_text("task_id", self.task_id),
        )
        object.__setattr__(self, "arm", _require_text("arm", self.arm))
        if self.terminal_state not in {"completed", "failed"}:
            raise GradeValidationError(
                "grade terminal commit must bind completed or failed state"
            )
        object.__setattr__(
            self,
            "terminal_state_hash",
            _require_sha256(
                "terminal_state_hash",
                self.terminal_state_hash,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **_grade_terminal_commit_payload(
                commit_id=self.commit_id,
                grade_id=self.grade_id,
                grade_revision_hash=self.grade_revision_hash,
                experiment_id=self.experiment_id,
                task_id=self.task_id,
                arm=self.arm,
                terminal_state=self.terminal_state,
                terminal_state_hash=self.terminal_state_hash,
                recorded_at_ms=self.recorded_at_ms,
            ),
            "commit_hash": self.commit_hash,
        }


@dataclass(frozen=True)
class DecisionGradeCitation:
    grade_id: str
    revision_hash: str
    acknowledged_invalidation_hashes: tuple[str, ...] = ()
    resolution_grade_id: str | None = None
    resolution_revision_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "grade_id", _require_text("grade_id", self.grade_id))
        object.__setattr__(
            self,
            "revision_hash",
            _require_sha256("revision_hash", self.revision_hash),
        )
        object.__setattr__(
            self,
            "acknowledged_invalidation_hashes",
            tuple(
                _require_sha256("invalidation_hash", value)
                for value in self.acknowledged_invalidation_hashes
            ),
        )
        resolution_fields = (
            self.resolution_grade_id,
            self.resolution_revision_hash,
        )
        if any(value is not None for value in resolution_fields):
            if not all(value is not None for value in resolution_fields):
                raise GradeValidationError(
                    "resolution grade id and revision hash must be recorded together"
                )
            object.__setattr__(
                self,
                "resolution_grade_id",
                _require_text(
                    "resolution_grade_id",
                    self.resolution_grade_id,
                ),
            )
            object.__setattr__(
                self,
                "resolution_revision_hash",
                _require_sha256(
                    "resolution_revision_hash",
                    self.resolution_revision_hash,
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade_id": self.grade_id,
            "revision_hash": self.revision_hash,
            "acknowledged_invalidation_hashes": list(
                self.acknowledged_invalidation_hashes
            ),
            "resolution_grade_id": self.resolution_grade_id,
            "resolution_revision_hash": self.resolution_revision_hash,
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "DecisionGradeCitation":
        acknowledgements = value.get(
            "acknowledged_invalidation_hashes",
            (),
        )
        if not isinstance(acknowledgements, (list, tuple)):
            raise GradeValidationError(
                "acknowledged_invalidation_hashes must be a list"
            )
        return cls(
            grade_id=str(value.get("grade_id") or ""),
            revision_hash=str(value.get("revision_hash") or ""),
            acknowledged_invalidation_hashes=tuple(
                str(item) for item in acknowledgements
            ),
            resolution_grade_id=(
                str(value["resolution_grade_id"])
                if value.get("resolution_grade_id") is not None
                else None
            ),
            resolution_revision_hash=(
                str(value["resolution_revision_hash"])
                if value.get("resolution_revision_hash") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class DecisionGradeBlocker:
    code: str
    grade_id: str
    detail: str
    invalidation_hashes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "grade_id": self.grade_id,
            "detail": self.detail,
            "invalidation_hashes": list(self.invalidation_hashes),
        }


@dataclass(frozen=True)
class DecisionGradeValidation:
    accepted: bool
    blockers: tuple[DecisionGradeBlocker, ...]
    schema_version: str = DECISION_GRADE_VALIDATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "accepted": self.accepted,
            "blockers": [blocker.to_dict() for blocker in self.blockers],
        }


@dataclass(frozen=True)
class GradeDecisionRecord:
    decision_id: str
    decision_hash: str
    decision: Mapping[str, Any]
    grade_citations: tuple[DecisionGradeCitation, ...]
    recorded_at_ms: int
    schema_version: str = GRADE_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision_id",
            _require_text("decision_id", self.decision_id),
        )
        object.__setattr__(
            self,
            "decision_hash",
            _require_sha256("decision_hash", self.decision_hash),
        )
        object.__setattr__(
            self,
            "decision",
            _freeze_json(
                _normalise_json(self.decision, field="decision")
            ),
        )
        object.__setattr__(
            self,
            "grade_citations",
            tuple(self.grade_citations),
        )
        if not self.grade_citations:
            raise GradeValidationError(
                "persisted decision requires grade citations"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "decision_hash": self.decision_hash,
            "decision": _thaw_json(self.decision),
            "grade_citations": [
                citation.to_dict() for citation in self.grade_citations
            ],
            "recorded_at_ms": self.recorded_at_ms,
        }


class GradeBook:
    """Append and validate immutable grade history behind one SQLite seam."""

    def __init__(self, path: str | Path) -> None:
        raw_path = str(path)
        if raw_path != ":memory:":
            resolved = Path(path).expanduser()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            raw_path = str(resolved)
        self.path = raw_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA recursive_triggers = ON")
        self._initialise_schema()

    def __enter__(self) -> "GradeBook":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def append_grade(
        self,
        *,
        run: RunEnvelopeRef,
        grade: Grade,
        verifier_config_hash: str,
        verifier_implementation_hash: str | None = None,
        supersedes_grade_id: str | None = None,
    ) -> GradeRevision:
        """Append one revision; never updates an existing grade."""
        return self._append_grade(
            run=run,
            grade=grade,
            verifier_config_hash=verifier_config_hash,
            verifier_implementation_hash=verifier_implementation_hash,
            supersedes_grade_id=supersedes_grade_id,
            supersession_reason="superseded_by_new_grade_revision",
        )

    def regrade(
        self,
        *,
        run: RunEnvelopeRef,
        grade: Grade,
        verifier_config_hash: str,
        supersedes_grade_id: str,
        reason: str,
        verifier_implementation_hash: str | None = None,
    ) -> GradeRevision:
        """Explicitly append a replacement grade to the current linear head."""
        return self._append_grade(
            run=run,
            grade=grade,
            verifier_config_hash=verifier_config_hash,
            verifier_implementation_hash=verifier_implementation_hash,
            supersedes_grade_id=_require_text(
                "supersedes_grade_id",
                supersedes_grade_id,
            ),
            supersession_reason=_require_text("reason", reason),
        )

    def _append_grade(
        self,
        *,
        run: RunEnvelopeRef,
        grade: Grade,
        verifier_config_hash: str,
        verifier_implementation_hash: str | None,
        supersedes_grade_id: str | None,
        supersession_reason: str,
    ) -> GradeRevision:
        config_hash = _require_sha256(
            "verifier_config_hash",
            verifier_config_hash,
        )
        implementation_hash = _require_sha256(
            "verifier_implementation_hash",
            verifier_implementation_hash or grade.verifier_hash,
        )
        _validate_grade(
            run=run,
            grade=grade,
            verifier_implementation_hash=implementation_hash,
        )
        evidence = _normalise_json(grade.evidence, field="evidence")
        grade_id = f"grade_{uuid.uuid4().hex}"
        recorded_at_ms = int(time.time_ns() // 1_000_000)

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if supersedes_grade_id is None:
                    existing = self._conn.execute(
                        """SELECT * FROM grade_revisions
                           WHERE run_id=? AND run_envelope_hash=?
                             AND frozen_result_hash=?
                           ORDER BY revision_number DESC LIMIT 1""",
                        (
                            run.run_id,
                            run.run_envelope_hash,
                            run.frozen_result_hash,
                        ),
                    ).fetchone()
                    if existing is not None:
                        revision = _revision_from_row(existing)
                        if _revision_matches_grade(
                            revision,
                            grade=grade,
                            verifier_config_hash=config_hash,
                            verifier_implementation_hash=implementation_hash,
                            evidence=evidence,
                        ):
                            self._conn.execute("COMMIT")
                            return revision
                revision_number = self._allocate_revision_number(
                    run=run,
                    supersedes_grade_id=supersedes_grade_id,
                )
                payload = _grade_revision_payload(
                    grade_id=grade_id,
                    revision_number=revision_number,
                    run_envelope=run,
                    verifier_id=grade.verifier_id,
                    verifier_version=grade.verifier_version,
                    verifier_config_hash=config_hash,
                    verifier_implementation_hash=implementation_hash,
                    passed=grade.passed,
                    score=grade.score,
                    evidence=evidence,
                    failure_classification=grade.failure_classification,
                    flake_classification=grade.flake_classification,
                    supersedes_grade_id=supersedes_grade_id,
                    recorded_at_ms=recorded_at_ms,
                )
                revision_hash = _sha256_json(payload)
                self._conn.execute(
                    """
                    INSERT INTO grade_revisions(
                      grade_id, revision_hash, revision_number,
                      run_id, run_envelope_hash, frozen_result_hash,
                      verifier_id, verifier_version,
                      verifier_config_hash, verifier_implementation_hash,
                      passed, score, evidence_json,
                      failure_classification, flake_classification,
                      supersedes_grade_id, recorded_at_ms
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        grade_id,
                        revision_hash,
                        revision_number,
                        run.run_id,
                        run.run_envelope_hash,
                        run.frozen_result_hash,
                        grade.verifier_id,
                        grade.verifier_version,
                        config_hash,
                        implementation_hash,
                        int(grade.passed),
                        float(grade.score),
                        _canonical_json(evidence),
                        str(grade.failure_classification),
                        str(grade.flake_classification),
                        supersedes_grade_id,
                        recorded_at_ms,
                    ),
                )
                if supersedes_grade_id is not None:
                    self._append_supersession_invalidation(
                        superseded_grade_id=supersedes_grade_id,
                        replacement_grade_id=grade_id,
                        replacement_revision_hash=revision_hash,
                        recorded_at_ms=recorded_at_ms,
                        reason=supersession_reason,
                    )
                self._conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                self._conn.execute("ROLLBACK")
                raise SupersessionConflict(
                    "grade append conflicts with immutable supersession history"
                ) from exc
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            return self.get_revision(grade_id)

    def get_revision(self, grade_id: str) -> GradeRevision:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM grade_revisions WHERE grade_id=?",
                (_require_text("grade_id", grade_id),),
            ).fetchone()
        if row is None:
            raise GradeNotFoundError(f"unknown grade revision: {grade_id}")
        return _revision_from_row(row)

    def list_revisions(self, run: RunEnvelopeRef) -> tuple[GradeRevision, ...]:
        """Return the complete append order for one exact run envelope."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM grade_revisions
                WHERE run_id=? AND run_envelope_hash=? AND frozen_result_hash=?
                ORDER BY revision_number ASC
                """,
                (
                    run.run_id,
                    run.run_envelope_hash,
                    run.frozen_result_hash,
                ),
            ).fetchall()
        return tuple(_revision_from_row(row) for row in rows)

    def list_invalidations(
        self,
        grade_id: str,
    ) -> tuple[GradeInvalidation, ...]:
        """Return every immutable invalidation recorded for a grade."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM grade_invalidations
                WHERE grade_id=?
                ORDER BY invalidation_sequence ASC
                """,
                (_require_text("grade_id", grade_id),),
            ).fetchall()
        return tuple(_invalidation_from_row(row) for row in rows)

    def commit_terminal_grade(
        self,
        *,
        grade_id: str,
        revision_hash: str,
        experiment_id: str,
        task_id: str,
        arm: str,
        terminal_state: str,
        terminal_state_hash: str,
    ) -> GradeTerminalCommit:
        """Authorize one revision from its durable terminal state."""
        target_id = _require_text("grade_id", grade_id)
        target_hash = _require_sha256("revision_hash", revision_hash)
        normalized_experiment_id = _require_text(
            "experiment_id",
            experiment_id,
        )
        normalized_task_id = _require_text("task_id", task_id)
        normalized_arm = _require_text("arm", arm)
        normalized_terminal_state = _require_text(
            "terminal_state",
            terminal_state,
        )
        if normalized_terminal_state not in {"completed", "failed"}:
            raise GradeValidationError(
                "grade terminal commit must bind completed or failed state"
            )
        normalized_terminal_hash = _require_sha256(
            "terminal_state_hash",
            terminal_state_hash,
        )
        recorded_at_ms = int(time.time_ns() // 1_000_000)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM grade_revisions WHERE grade_id=?",
                    (target_id,),
                ).fetchone()
                if row is None:
                    raise GradeNotFoundError(
                        f"cannot commit unknown grade: {target_id}"
                    )
                revision = _revision_from_row(row)
                if revision.revision_hash != target_hash:
                    raise GradeValidationError(
                        "terminal commit does not pin the stored grade revision"
                    )
                if revision.passed and normalized_terminal_state != "completed":
                    raise GradeValidationError(
                        "passing grade terminal commit must bind completed state"
                    )
                expected_lineage_identity = (
                    normalized_experiment_id,
                    normalized_task_id,
                    normalized_arm,
                    normalized_terminal_state,
                    normalized_terminal_hash,
                )
                lineage_rows = self._conn.execute(
                    """
                    SELECT grade_terminal_commits.*
                    FROM grade_terminal_commits
                    JOIN grade_revisions
                      ON grade_revisions.grade_id
                       = grade_terminal_commits.grade_id
                    WHERE grade_revisions.run_id=?
                      AND grade_revisions.run_envelope_hash=?
                      AND grade_revisions.frozen_result_hash=?
                      AND grade_terminal_commits.grade_id<>?
                    """,
                    (
                        revision.run_envelope.run_id,
                        revision.run_envelope.run_envelope_hash,
                        revision.run_envelope.frozen_result_hash,
                        target_id,
                    ),
                ).fetchall()
                for lineage_row in lineage_rows:
                    lineage_commit = _terminal_commit_from_row(lineage_row)
                    lineage_identity = (
                        lineage_commit.experiment_id,
                        lineage_commit.task_id,
                        lineage_commit.arm,
                        lineage_commit.terminal_state,
                        lineage_commit.terminal_state_hash,
                    )
                    if lineage_identity != expected_lineage_identity:
                        raise GradeValidationError(
                            "grade supersession terminal identity discrepancy"
                        )
                existing = self._conn.execute(
                    """
                    SELECT * FROM grade_terminal_commits
                    WHERE grade_id=?
                    """,
                    (target_id,),
                ).fetchone()
                if existing is not None:
                    commit = _terminal_commit_from_row(existing)
                    expected = (
                        target_hash,
                        normalized_experiment_id,
                        normalized_task_id,
                        normalized_arm,
                        normalized_terminal_state,
                        normalized_terminal_hash,
                    )
                    actual = (
                        commit.grade_revision_hash,
                        commit.experiment_id,
                        commit.task_id,
                        commit.arm,
                        commit.terminal_state,
                        commit.terminal_state_hash,
                    )
                    if actual != expected:
                        raise GradeValidationError(
                            "persisted grade terminal commit discrepancy"
                        )
                    self._conn.execute("COMMIT")
                    return commit
                commit_id = f"terminal_commit_{uuid.uuid4().hex}"
                payload = _grade_terminal_commit_payload(
                    commit_id=commit_id,
                    grade_id=target_id,
                    grade_revision_hash=target_hash,
                    experiment_id=normalized_experiment_id,
                    task_id=normalized_task_id,
                    arm=normalized_arm,
                    terminal_state=normalized_terminal_state,
                    terminal_state_hash=normalized_terminal_hash,
                    recorded_at_ms=recorded_at_ms,
                )
                commit_hash = _sha256_json(payload)
                self._conn.execute(
                    """
                    INSERT INTO grade_terminal_commits(
                      commit_id, commit_hash,
                      grade_id, grade_revision_hash,
                      experiment_id, task_id, arm,
                      terminal_state, terminal_state_hash,
                      recorded_at_ms
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        commit_id,
                        commit_hash,
                        target_id,
                        target_hash,
                        normalized_experiment_id,
                        normalized_task_id,
                        normalized_arm,
                        normalized_terminal_state,
                        normalized_terminal_hash,
                        recorded_at_ms,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        commit = self.get_terminal_commit(target_id)
        if commit is None:
            raise GradeIntegrityError(
                f"grade terminal commit disappeared for {target_id}"
            )
        return commit

    def get_terminal_commit(
        self,
        grade_id: str,
    ) -> GradeTerminalCommit | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM grade_terminal_commits
                WHERE grade_id=?
                """,
                (_require_text("grade_id", grade_id),),
            ).fetchone()
        return None if row is None else _terminal_commit_from_row(row)

    def list_uncommitted_passing_revisions(
        self,
    ) -> tuple[GradeRevision, ...]:
        """Return passing revisions that lack positive terminal authority."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT grade_revisions.*
                FROM grade_revisions
                LEFT JOIN grade_terminal_commits
                  ON grade_terminal_commits.grade_id
                   = grade_revisions.grade_id
                WHERE grade_revisions.passed = 1
                  AND grade_terminal_commits.grade_id IS NULL
                ORDER BY grade_revisions.recorded_at_ms, grade_revisions.grade_id
                """
            ).fetchall()
        return tuple(_revision_from_row(row) for row in rows)

    def list_uncommitted_revisions(self) -> tuple[GradeRevision, ...]:
        """Return every revision that lacks positive terminal authority."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT grade_revisions.*
                FROM grade_revisions
                LEFT JOIN grade_terminal_commits
                  ON grade_terminal_commits.grade_id
                   = grade_revisions.grade_id
                WHERE grade_terminal_commits.grade_id IS NULL
                ORDER BY grade_revisions.recorded_at_ms, grade_revisions.grade_id
                """
            ).fetchall()
        return tuple(_revision_from_row(row) for row in rows)

    def to_trace_graph(
        self,
        run: RunEnvelopeRef,
        *,
        namespace: str = DEFAULT_GRADE_TRACE_NAMESPACE,
    ) -> TraceGraph:
        """Project one exact run's immutable grade lineage into trace nodes."""
        return project_gradebook_to_trace(
            self,
            run,
            namespace=namespace,
        )

    def project_trace(
        self,
        run: RunEnvelopeRef,
        *,
        namespace: str = DEFAULT_GRADE_TRACE_NAMESPACE,
    ) -> TraceGraph:
        """Compatibility alias for the public GradeBook trace projection."""
        return self.to_trace_graph(run, namespace=namespace)

    def invalidate_grade(
        self,
        grade_id: str,
        *,
        reason: str,
    ) -> GradeInvalidation:
        """Append a non-supersession invalidation without altering the grade."""
        return self._append_standalone_invalidation(
            grade_id,
            kind="invalidated",
            reason=reason,
        )

    def quarantine_grade(
        self,
        grade_id: str,
        *,
        reason: str,
    ) -> GradeInvalidation:
        """Durably quarantine a revision after compensation fails."""
        return self._append_standalone_invalidation(
            grade_id,
            kind="quarantined",
            reason=reason,
        )

    def _append_standalone_invalidation(
        self,
        grade_id: str,
        *,
        kind: str,
        reason: str,
    ) -> GradeInvalidation:
        target_id = _require_text("grade_id", grade_id)
        invalidation_reason = _require_text("reason", reason)
        recorded_at_ms = int(time.time_ns() // 1_000_000)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                target = self._conn.execute(
                    """
                    SELECT grade_id, revision_hash
                    FROM grade_revisions
                    WHERE grade_id=?
                    """,
                    (target_id,),
                ).fetchone()
                if target is None:
                    raise GradeNotFoundError(
                        f"cannot invalidate unknown grade: {target_id}"
                    )
                invalidation = self._insert_invalidation(
                    grade_id=target_id,
                    grade_revision_hash=str(target["revision_hash"]),
                    kind=_require_text("kind", kind),
                    reason=invalidation_reason,
                    replacement_grade_id=None,
                    replacement_revision_hash=None,
                    recorded_at_ms=recorded_at_ms,
                )
                self._conn.execute("COMMIT")
                return invalidation
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def validate_decision(
        self,
        citations: Iterable[DecisionGradeCitation],
    ) -> DecisionGradeValidation:
        """Block unknown, hash-mismatched, or unacknowledged stale citations."""
        citation_list = tuple(citations)
        blockers: list[DecisionGradeBlocker] = []
        if not citation_list:
            blockers.append(
                DecisionGradeBlocker(
                    code="missing_grade_citation",
                    grade_id="",
                    detail="decision does not cite an immutable grade revision",
                )
            )
        for citation in citation_list:
            try:
                revision = self.get_revision(citation.grade_id)
            except GradeNotFoundError:
                blockers.append(
                    DecisionGradeBlocker(
                        code="unknown_grade",
                        grade_id=citation.grade_id,
                        detail="cited grade does not exist",
                    )
                )
                continue
            except (GradeIntegrityError, GradeValidationError) as exc:
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_integrity_failure",
                        grade_id=citation.grade_id,
                        detail=str(exc),
                    )
                )
                continue

            if revision.revision_hash != citation.revision_hash:
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_revision_hash_mismatch",
                        grade_id=citation.grade_id,
                        detail="citation does not pin the stored grade revision",
                    )
                )
                continue
            if not (
                revision.verifier_id
                and revision.verifier_version
                and revision.verifier_config_hash
                and revision.verifier_implementation_hash
            ):
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_missing_verifier_provenance",
                        grade_id=citation.grade_id,
                        detail="a scored grade must pin verifier provenance",
                    )
                )
            terminal_blocker = self._terminal_commit_blocker(
                revision
            )
            if terminal_blocker is not None:
                blockers.append(terminal_blocker)
                continue

            try:
                invalidations = self.list_invalidations(citation.grade_id)
            except (GradeIntegrityError, GradeValidationError) as exc:
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_invalidation_integrity_failure",
                        grade_id=citation.grade_id,
                        detail=str(exc),
                    )
                )
                continue
            actual = {
                invalidation.invalidation_hash
                for invalidation in invalidations
            }
            acknowledged = set(citation.acknowledged_invalidation_hashes)
            unknown_acknowledgements = tuple(sorted(acknowledged - actual))
            if unknown_acknowledgements:
                blockers.append(
                    DecisionGradeBlocker(
                        code="unknown_invalidation_acknowledgement",
                        grade_id=citation.grade_id,
                        detail=(
                            "citation acknowledges invalidations not recorded "
                            "for this grade"
                        ),
                        invalidation_hashes=unknown_acknowledgements,
                    )
                )
            missing_acknowledgements = tuple(sorted(actual - acknowledged))
            if missing_acknowledgements:
                blockers.append(
                    DecisionGradeBlocker(
                        code="stale_grade_unacknowledged",
                        grade_id=citation.grade_id,
                        detail=(
                            "cited grade is stale; acknowledge every immutable "
                            "invalidation hash explicitly"
                        ),
                        invalidation_hashes=missing_acknowledgements,
                    )
                )
                continue
            if not actual:
                continue
            if (
                citation.resolution_grade_id is None
                or citation.resolution_revision_hash is None
            ):
                blockers.append(
                    DecisionGradeBlocker(
                        code="stale_grade_unresolved",
                        grade_id=citation.grade_id,
                        detail=(
                            "cited grade is stale; acknowledgement must resolve "
                            "to the current non-invalidated grade revision"
                        ),
                        invalidation_hashes=tuple(sorted(actual)),
                    )
                )
                continue
            try:
                resolution = self.get_revision(
                    citation.resolution_grade_id
                )
            except GradeNotFoundError:
                blockers.append(
                    DecisionGradeBlocker(
                        code="unknown_grade_resolution",
                        grade_id=citation.grade_id,
                        detail="grade resolution does not exist",
                    )
                )
                continue
            except (GradeIntegrityError, GradeValidationError) as exc:
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_resolution_integrity_failure",
                        grade_id=citation.grade_id,
                        detail=str(exc),
                    )
                )
                continue
            if (
                resolution.revision_hash
                != citation.resolution_revision_hash
            ):
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_resolution_hash_mismatch",
                        grade_id=citation.grade_id,
                        detail=(
                            "grade resolution does not pin the stored revision"
                        ),
                    )
                )
                continue
            if resolution.run_envelope != revision.run_envelope:
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_resolution_wrong_run",
                        grade_id=citation.grade_id,
                        detail=(
                            "grade resolution belongs to a different run "
                            "envelope"
                        ),
                    )
                )
                continue
            history = self.list_revisions(revision.run_envelope)
            if not history or history[-1].grade_id != resolution.grade_id:
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_resolution_not_current",
                        grade_id=citation.grade_id,
                        detail=(
                            "grade resolution is not the current revision for "
                            "the run envelope"
                        ),
                    )
                )
                continue
            resolution_invalidations = self.list_invalidations(
                resolution.grade_id
            )
            if resolution_invalidations:
                blockers.append(
                    DecisionGradeBlocker(
                        code="grade_resolution_invalidated",
                        grade_id=citation.grade_id,
                        detail=(
                            "grade resolution is itself invalidated or "
                            "superseded"
                        ),
                        invalidation_hashes=tuple(
                            invalidation.invalidation_hash
                            for invalidation in resolution_invalidations
                        ),
                    )
                )
                continue
            resolution_terminal_blocker = (
                self._terminal_commit_blocker(resolution)
            )
            if resolution_terminal_blocker is not None:
                blockers.append(resolution_terminal_blocker)
        return DecisionGradeValidation(
            accepted=not blockers,
            blockers=tuple(blockers),
        )

    def _terminal_commit_blocker(
        self,
        revision: GradeRevision,
    ) -> DecisionGradeBlocker | None:
        try:
            commit = self.get_terminal_commit(revision.grade_id)
        except (GradeIntegrityError, GradeValidationError) as exc:
            return DecisionGradeBlocker(
                code="grade_terminal_commit_integrity_failure",
                grade_id=revision.grade_id,
                detail=str(exc),
            )
        if commit is None:
            return DecisionGradeBlocker(
                code="grade_terminal_commit_missing",
                grade_id=revision.grade_id,
                detail="grade lacks a durable terminal commit",
            )
        if commit.grade_revision_hash != revision.revision_hash:
            return DecisionGradeBlocker(
                code="grade_terminal_commit_revision_mismatch",
                grade_id=revision.grade_id,
                detail="terminal commit does not pin the grade revision",
            )
        if revision.passed and commit.terminal_state != "completed":
            return DecisionGradeBlocker(
                code="grade_terminal_commit_state_mismatch",
                grade_id=revision.grade_id,
                detail="passing grade terminal commit is not completed",
            )
        return None

    def record_decision(
        self,
        *,
        decision_id: str,
        decision: Mapping[str, Any],
        citations: Iterable[DecisionGradeCitation],
    ) -> GradeDecisionRecord:
        """Persist an immutable decision only after exact grade-lineage checks."""
        normalized_id = _require_text("decision_id", decision_id)
        normalized_decision = _normalise_json(
            decision,
            field="decision",
        )
        citation_list = tuple(citations)
        validation = self.validate_decision(citation_list)
        if not validation.accepted:
            codes = ", ".join(
                blocker.code for blocker in validation.blockers
            )
            raise GradeValidationError(
                "decision grade lineage is invalid: " + codes
            )
        recorded_at_ms = int(time.time_ns() // 1_000_000)
        payload = {
            "schema_version": GRADE_DECISION_SCHEMA_VERSION,
            "decision_id": normalized_id,
            "decision": normalized_decision,
            "grade_citations": [
                citation.to_dict() for citation in citation_list
            ],
            "recorded_at_ms": recorded_at_ms,
        }
        decision_hash = _sha256_json(payload)
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM grade_decisions WHERE decision_id=?",
                (normalized_id,),
            ).fetchone()
            if existing is not None:
                record = _decision_from_row(existing)
                comparable = {
                    **record.to_dict(),
                    "decision_hash": decision_hash,
                    "recorded_at_ms": recorded_at_ms,
                }
                if (
                    comparable["decision"] == normalized_decision
                    and comparable["grade_citations"]
                    == payload["grade_citations"]
                ):
                    return record
                raise GradeValidationError(
                    "persisted grade-backed decision discrepancy"
                )
            self._conn.execute(
                """INSERT INTO grade_decisions(
                     decision_id, decision_hash, decision_json,
                     grade_citations_json, recorded_at_ms
                   ) VALUES(?, ?, ?, ?, ?)""",
                (
                    normalized_id,
                    decision_hash,
                    _canonical_json(normalized_decision),
                    _canonical_json(payload["grade_citations"]),
                    recorded_at_ms,
                ),
            )
        return self.get_decision(normalized_id)

    def get_decision(self, decision_id: str) -> GradeDecisionRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM grade_decisions WHERE decision_id=?",
                (_require_text("decision_id", decision_id),),
            ).fetchone()
        if row is None:
            raise GradeNotFoundError(
                f"unknown grade-backed decision: {decision_id}"
            )
        return _decision_from_row(row)

    def _allocate_revision_number(
        self,
        *,
        run: RunEnvelopeRef,
        supersedes_grade_id: str | None,
    ) -> int:
        if supersedes_grade_id is None:
            existing = self._conn.execute(
                """
                SELECT grade_id FROM grade_revisions
                WHERE run_envelope_hash=?
                LIMIT 1
                """,
                (run.run_envelope_hash,),
            ).fetchone()
            if existing is not None:
                raise SupersessionConflict(
                    "run envelope already has a grade; append must supersede its head"
                )
            return 1

        target = self._conn.execute(
            "SELECT * FROM grade_revisions WHERE grade_id=?",
            (_require_text("supersedes_grade_id", supersedes_grade_id),),
        ).fetchone()
        if target is None:
            raise GradeNotFoundError(
                f"cannot supersede unknown grade: {supersedes_grade_id}"
            )
        if (
            str(target["run_id"]) != run.run_id
            or str(target["run_envelope_hash"]) != run.run_envelope_hash
            or str(target["frozen_result_hash"]) != run.frozen_result_hash
        ):
            raise SupersessionConflict(
                "a grade can only supersede the current head for the same run envelope"
            )
        child = self._conn.execute(
            """
            SELECT grade_id FROM grade_revisions
            WHERE supersedes_grade_id=?
            LIMIT 1
            """,
            (supersedes_grade_id,),
        ).fetchone()
        if child is not None:
            raise SupersessionConflict(
                f"grade {supersedes_grade_id} has already been superseded"
            )
        return int(target["revision_number"]) + 1

    def _append_supersession_invalidation(
        self,
        *,
        superseded_grade_id: str,
        replacement_grade_id: str,
        replacement_revision_hash: str,
        recorded_at_ms: int,
        reason: str,
    ) -> None:
        target = self._conn.execute(
            "SELECT revision_hash FROM grade_revisions WHERE grade_id=?",
            (superseded_grade_id,),
        ).fetchone()
        if target is None:
            raise GradeNotFoundError(
                f"cannot invalidate unknown grade: {superseded_grade_id}"
            )
        self._insert_invalidation(
            grade_id=superseded_grade_id,
            grade_revision_hash=str(target["revision_hash"]),
            kind="superseded",
            reason=reason,
            replacement_grade_id=replacement_grade_id,
            replacement_revision_hash=replacement_revision_hash,
            recorded_at_ms=recorded_at_ms,
        )

    def _insert_invalidation(
        self,
        *,
        grade_id: str,
        grade_revision_hash: str,
        kind: str,
        reason: str,
        replacement_grade_id: str | None,
        replacement_revision_hash: str | None,
        recorded_at_ms: int,
    ) -> GradeInvalidation:
        invalidation_id = f"invalidation_{uuid.uuid4().hex}"
        payload = _grade_invalidation_payload(
            invalidation_id=invalidation_id,
            grade_id=grade_id,
            grade_revision_hash=grade_revision_hash,
            kind=kind,
            reason=reason,
            replacement_grade_id=replacement_grade_id,
            replacement_revision_hash=replacement_revision_hash,
            recorded_at_ms=recorded_at_ms,
        )
        invalidation_hash = _sha256_json(payload)
        self._conn.execute(
            """
            INSERT INTO grade_invalidations(
              invalidation_id, invalidation_hash,
              grade_id, grade_revision_hash,
              kind, reason,
              replacement_grade_id, replacement_revision_hash,
              recorded_at_ms
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invalidation_id,
                invalidation_hash,
                grade_id,
                grade_revision_hash,
                kind,
                reason,
                replacement_grade_id,
                replacement_revision_hash,
                recorded_at_ms,
            ),
        )
        return GradeInvalidation(
            invalidation_id=invalidation_id,
            invalidation_hash=invalidation_hash,
            grade_id=grade_id,
            grade_revision_hash=grade_revision_hash,
            kind=kind,
            reason=reason,
            replacement_grade_id=replacement_grade_id,
            replacement_revision_hash=replacement_revision_hash,
            recorded_at_ms=recorded_at_ms,
        )

    def _initialise_schema(self) -> None:
        with self._lock:
            self._upgrade_grade_terminal_commit_schema()
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS grade_revisions (
                  grade_id TEXT PRIMARY KEY,
                  revision_hash TEXT NOT NULL UNIQUE,
                  revision_number INTEGER NOT NULL CHECK(revision_number > 0),
                  run_id TEXT NOT NULL,
                  run_envelope_hash TEXT NOT NULL,
                  frozen_result_hash TEXT NOT NULL,
                  verifier_id TEXT NOT NULL CHECK(length(verifier_id) > 0),
                  verifier_version TEXT NOT NULL CHECK(length(verifier_version) > 0),
                  verifier_config_hash TEXT NOT NULL,
                  verifier_implementation_hash TEXT NOT NULL,
                  passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
                  score REAL NOT NULL,
                  evidence_json TEXT NOT NULL,
                  failure_classification TEXT NOT NULL,
                  flake_classification TEXT NOT NULL,
                  supersedes_grade_id TEXT REFERENCES grade_revisions(grade_id),
                  recorded_at_ms INTEGER NOT NULL,
                  UNIQUE(run_envelope_hash, revision_number)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_grade_root_per_run_envelope
                  ON grade_revisions(run_envelope_hash)
                  WHERE supersedes_grade_id IS NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS one_child_per_grade_revision
                  ON grade_revisions(supersedes_grade_id)
                  WHERE supersedes_grade_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS grade_invalidations (
                  invalidation_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  invalidation_id TEXT NOT NULL UNIQUE,
                  invalidation_hash TEXT NOT NULL UNIQUE,
                  grade_id TEXT NOT NULL REFERENCES grade_revisions(grade_id),
                  grade_revision_hash TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  reason TEXT NOT NULL CHECK(length(reason) > 0),
                  replacement_grade_id TEXT REFERENCES grade_revisions(grade_id),
                  replacement_revision_hash TEXT,
                  recorded_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS grade_terminal_commits (
                  commit_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  commit_id TEXT NOT NULL UNIQUE,
                  commit_hash TEXT NOT NULL UNIQUE,
                  grade_id TEXT NOT NULL UNIQUE
                    REFERENCES grade_revisions(grade_id),
                  grade_revision_hash TEXT NOT NULL,
                  experiment_id TEXT NOT NULL,
                  task_id TEXT NOT NULL,
                  arm TEXT NOT NULL,
                  terminal_state TEXT NOT NULL
                    CHECK(terminal_state IN ('completed', 'failed')),
                  terminal_state_hash TEXT NOT NULL,
                  recorded_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS grade_decisions (
                  decision_id TEXT PRIMARY KEY,
                  decision_hash TEXT NOT NULL UNIQUE,
                  decision_json TEXT NOT NULL,
                  grade_citations_json TEXT NOT NULL,
                  recorded_at_ms INTEGER NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS grade_decisions_no_replace
                BEFORE INSERT ON grade_decisions
                WHEN EXISTS (
                  SELECT 1
                    FROM grade_decisions
                   WHERE decision_id = NEW.decision_id
                      OR decision_hash = NEW.decision_hash
                )
                BEGIN
                  SELECT RAISE(ABORT, 'grade decisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS grade_revisions_no_replace
                BEFORE INSERT ON grade_revisions
                WHEN EXISTS (
                  SELECT 1
                    FROM grade_revisions
                   WHERE grade_id = NEW.grade_id
                      OR revision_hash = NEW.revision_hash
                      OR (
                           run_envelope_hash = NEW.run_envelope_hash
                       AND revision_number = NEW.revision_number
                         )
                      OR (
                           NEW.supersedes_grade_id IS NULL
                       AND run_envelope_hash = NEW.run_envelope_hash
                       AND supersedes_grade_id IS NULL
                         )
                      OR (
                           NEW.supersedes_grade_id IS NOT NULL
                       AND supersedes_grade_id = NEW.supersedes_grade_id
                         )
                )
                BEGIN
                  SELECT RAISE(ABORT, 'grade revisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS grade_revisions_no_update
                BEFORE UPDATE ON grade_revisions
                BEGIN
                  SELECT RAISE(ABORT, 'grade revisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS grade_revisions_no_delete
                BEFORE DELETE ON grade_revisions
                BEGIN
                  SELECT RAISE(ABORT, 'grade revisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS grade_invalidations_no_replace
                BEFORE INSERT ON grade_invalidations
                WHEN EXISTS (
                  SELECT 1
                    FROM grade_invalidations
                   WHERE (
                           NEW.invalidation_sequence > 0
                       AND invalidation_sequence = NEW.invalidation_sequence
                         )
                      OR invalidation_id = NEW.invalidation_id
                      OR invalidation_hash = NEW.invalidation_hash
                )
                BEGIN
                  SELECT RAISE(ABORT, 'grade invalidations are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS grade_invalidations_no_update
                BEFORE UPDATE ON grade_invalidations
                BEGIN
                  SELECT RAISE(ABORT, 'grade invalidations are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS grade_invalidations_no_delete
                BEFORE DELETE ON grade_invalidations
                BEGIN
                  SELECT RAISE(ABORT, 'grade invalidations are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS grade_terminal_commits_no_replace
                BEFORE INSERT ON grade_terminal_commits
                WHEN EXISTS (
                  SELECT 1
                    FROM grade_terminal_commits
                   WHERE commit_id = NEW.commit_id
                      OR commit_hash = NEW.commit_hash
                      OR grade_id = NEW.grade_id
                )
                BEGIN
                  SELECT RAISE(
                    ABORT,
                    'grade terminal commits are immutable'
                  );
                END;

                CREATE TRIGGER IF NOT EXISTS
                  grade_terminal_commits_lineage_identity
                BEFORE INSERT ON grade_terminal_commits
                WHEN EXISTS (
                  SELECT 1
                    FROM grade_terminal_commits AS existing_commit
                    JOIN grade_revisions AS existing_revision
                      ON existing_revision.grade_id
                       = existing_commit.grade_id
                    JOIN grade_revisions AS new_revision
                      ON new_revision.grade_id = NEW.grade_id
                   WHERE existing_revision.run_id = new_revision.run_id
                     AND existing_revision.run_envelope_hash
                         = new_revision.run_envelope_hash
                     AND existing_revision.frozen_result_hash
                         = new_revision.frozen_result_hash
                     AND (
                          existing_commit.experiment_id != NEW.experiment_id
                       OR existing_commit.task_id != NEW.task_id
                       OR existing_commit.arm != NEW.arm
                       OR existing_commit.terminal_state != NEW.terminal_state
                       OR existing_commit.terminal_state_hash
                          != NEW.terminal_state_hash
                     )
                )
                BEGIN
                  SELECT RAISE(
                    ABORT,
                    'grade supersession terminal identity discrepancy'
                  );
                END;

                CREATE TRIGGER IF NOT EXISTS grade_terminal_commits_no_update
                BEFORE UPDATE ON grade_terminal_commits
                BEGIN
                  SELECT RAISE(
                    ABORT,
                    'grade terminal commits are immutable'
                  );
                END;

                CREATE TRIGGER IF NOT EXISTS grade_terminal_commits_no_delete
                BEFORE DELETE ON grade_terminal_commits
                BEGIN
                  SELECT RAISE(
                    ABORT,
                    'grade terminal commits are immutable'
                  );
                END;

                CREATE TRIGGER IF NOT EXISTS grade_decisions_no_update
                BEFORE UPDATE ON grade_decisions
                BEGIN
                  SELECT RAISE(ABORT, 'grade decisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS grade_decisions_no_delete
                BEFORE DELETE ON grade_decisions
                BEGIN
                  SELECT RAISE(ABORT, 'grade decisions are immutable');
                END;
                """
            )
            self._validate_terminal_commit_lineages()

    def _validate_terminal_commit_lineages(self) -> None:
        rows = self._conn.execute(
            """
            SELECT grade_terminal_commits.*,
                   grade_revisions.run_id,
                   grade_revisions.run_envelope_hash,
                   grade_revisions.frozen_result_hash
            FROM grade_terminal_commits
            JOIN grade_revisions
              ON grade_revisions.grade_id
               = grade_terminal_commits.grade_id
            ORDER BY grade_terminal_commits.commit_sequence
            """
        ).fetchall()
        identities: dict[
            tuple[str, str, str],
            tuple[str, str, str, str, str],
        ] = {}
        for row in rows:
            commit = _terminal_commit_from_row(row)
            lineage = (
                str(row["run_id"]),
                str(row["run_envelope_hash"]),
                str(row["frozen_result_hash"]),
            )
            identity = (
                commit.experiment_id,
                commit.task_id,
                commit.arm,
                commit.terminal_state,
                commit.terminal_state_hash,
            )
            existing = identities.setdefault(lineage, identity)
            if existing != identity:
                raise GradeIntegrityError(
                    "grade supersession terminal identity discrepancy "
                    "in persisted lineage"
                )

    def _upgrade_grade_terminal_commit_schema(self) -> None:
        table = self._conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type='table' AND name='grade_terminal_commits'
            """
        ).fetchone()
        if table is None:
            return
        table_sql = str(table["sql"] or "").casefold()
        terminal_hash_is_unique = False
        for index in self._conn.execute(
            "PRAGMA index_list('grade_terminal_commits')"
        ).fetchall():
            if not bool(index["unique"]):
                continue
            index_name = str(index["name"]).replace('"', '""')
            columns = self._conn.execute(
                f'PRAGMA index_info("{index_name}")'
            ).fetchall()
            if [str(column["name"]) for column in columns] == [
                "terminal_state_hash"
            ]:
                terminal_hash_is_unique = True
                break
        if "'failed'" in table_sql and not terminal_hash_is_unique:
            return

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for trigger_name in (
                "grade_terminal_commits_no_replace",
                "grade_terminal_commits_lineage_identity",
                "grade_terminal_commits_no_update",
                "grade_terminal_commits_no_delete",
            ):
                self._conn.execute(
                    f'DROP TRIGGER IF EXISTS "{trigger_name}"'
                )
            self._conn.execute(
                """
                ALTER TABLE grade_terminal_commits
                RENAME TO grade_terminal_commits_legacy
                """
            )
            self._conn.execute(
                """
                CREATE TABLE grade_terminal_commits (
                  commit_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  commit_id TEXT NOT NULL UNIQUE,
                  commit_hash TEXT NOT NULL UNIQUE,
                  grade_id TEXT NOT NULL UNIQUE
                    REFERENCES grade_revisions(grade_id),
                  grade_revision_hash TEXT NOT NULL,
                  experiment_id TEXT NOT NULL,
                  task_id TEXT NOT NULL,
                  arm TEXT NOT NULL,
                  terminal_state TEXT NOT NULL
                    CHECK(terminal_state IN ('completed', 'failed')),
                  terminal_state_hash TEXT NOT NULL,
                  recorded_at_ms INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                INSERT INTO grade_terminal_commits(
                  commit_sequence, commit_id, commit_hash,
                  grade_id, grade_revision_hash,
                  experiment_id, task_id, arm,
                  terminal_state, terminal_state_hash,
                  recorded_at_ms
                )
                SELECT
                  commit_sequence, commit_id, commit_hash,
                  grade_id, grade_revision_hash,
                  experiment_id, task_id, arm,
                  terminal_state, terminal_state_hash,
                  recorded_at_ms
                FROM grade_terminal_commits_legacy
                """
            )
            self._conn.execute(
                "DROP TABLE grade_terminal_commits_legacy"
            )
            self._validate_terminal_commit_lineages()
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise


def project_gradebook_to_trace(
    gradebook: GradeBook,
    run: RunEnvelopeRef,
    *,
    namespace: str = DEFAULT_GRADE_TRACE_NAMESPACE,
) -> TraceGraph:
    """Return immutable GRADE nodes and exact supersession/invalidation edges."""
    revisions = gradebook.list_revisions(run)
    revisions_by_id = {
        revision.grade_id: revision for revision in revisions
    }
    revision_nodes: dict[str, TraceNode] = {}
    nodes: list[TraceNode] = []
    edges: list[TraceEdge] = []

    for revision in revisions:
        node = TraceNode(
            identity=TraceIdentity(
                namespace=namespace,
                node_type=NodeType.GRADE,
                logical_id=f"GRADE-{revision.grade_id}",
                revision_hash=revision.revision_hash,
                instance_id=trace_instance_id_from_hash(
                    timestamp_ms=revision.recorded_at_ms,
                    content_hash=revision.revision_hash,
                    domain="supervisor-grade-revision",
                ),
            ),
            verifier_id=revision.verifier_id,
            verifier_revision_hash=(
                revision.verifier_implementation_hash
            ),
            attributes={
                "record_kind": "grade_revision",
                **revision.to_dict(),
            },
        )
        revision_nodes[revision.grade_id] = node
        nodes.append(node)

    for revision in revisions:
        if revision.supersedes_grade_id is None:
            continue
        superseded = revision_nodes.get(revision.supersedes_grade_id)
        if superseded is None:
            raise GradeIntegrityError(
                "grade trace projection references a superseded revision "
                f"outside the exact run: {revision.supersedes_grade_id}"
            )
        edges.append(
            TraceEdge(
                source=revision_nodes[revision.grade_id].identity,
                relation=EdgeType.SUPERSEDES,
                target=superseded.identity,
            )
        )

    for revision in revisions:
        target_node = revision_nodes[revision.grade_id]
        for invalidation in gradebook.list_invalidations(
            revision.grade_id
        ):
            if invalidation.grade_revision_hash != revision.revision_hash:
                raise GradeIntegrityError(
                    "grade invalidation does not pin the target revision hash"
                )
            if invalidation.replacement_grade_id is not None:
                replacement = revisions_by_id.get(
                    invalidation.replacement_grade_id
                )
                if replacement is None:
                    raise GradeIntegrityError(
                        "grade invalidation replacement is outside the exact run"
                    )
                if (
                    replacement.revision_hash
                    != invalidation.replacement_revision_hash
                ):
                    raise GradeIntegrityError(
                        "grade invalidation replacement hash mismatch"
                    )
            invalidation_node = TraceNode(
                identity=TraceIdentity(
                    namespace=namespace,
                    node_type=NodeType.GRADE,
                    logical_id=(
                        "GRADE-INVALIDATION-"
                        f"{invalidation.invalidation_id}"
                    ),
                    revision_hash=invalidation.invalidation_hash,
                    instance_id=trace_instance_id_from_hash(
                        timestamp_ms=invalidation.recorded_at_ms,
                        content_hash=invalidation.invalidation_hash,
                        domain="supervisor-grade-invalidation",
                    ),
                ),
                verifier_id=revision.verifier_id,
                verifier_revision_hash=(
                    revision.verifier_implementation_hash
                ),
                attributes={
                    "record_kind": "grade_invalidation",
                    **invalidation.to_dict(),
                },
            )
            nodes.append(invalidation_node)
            edges.append(
                TraceEdge(
                    source=invalidation_node.identity,
                    relation=EdgeType.INVALIDATES,
                    target=target_node.identity,
                )
            )

    return TraceGraph(nodes=nodes, edges=edges)


def _validate_grade(
    *,
    run: RunEnvelopeRef,
    grade: Grade,
    verifier_implementation_hash: str,
) -> None:
    _require_text("verifier_id", grade.verifier_id)
    _require_text("verifier_version", grade.verifier_version)
    grade_hash = _require_sha256("grade.verifier_hash", grade.verifier_hash)
    if grade_hash != verifier_implementation_hash:
        raise GradeValidationError(
            "Grade.verifier_hash does not match verifier_implementation_hash"
        )
    frozen_result_hash = _require_sha256(
        "grade.frozen_result_hash",
        grade.frozen_result_hash,
    )
    if frozen_result_hash != run.frozen_result_hash:
        raise GradeValidationError(
            "grade does not reference the RunEnvelopeRef frozen result"
        )
    if not isinstance(grade.passed, bool):
        raise GradeValidationError("grade.passed must be a bool")
    _require_score(grade.score)
    if not isinstance(grade.evidence, Mapping):
        raise GradeValidationError("grade.evidence must be a mapping")


def _revision_matches_grade(
    revision: GradeRevision,
    *,
    grade: Grade,
    verifier_config_hash: str,
    verifier_implementation_hash: str,
    evidence: Mapping[str, Any],
) -> bool:
    return (
        revision.verifier_id == grade.verifier_id
        and revision.verifier_version == grade.verifier_version
        and revision.verifier_config_hash == verifier_config_hash
        and revision.verifier_implementation_hash
        == verifier_implementation_hash
        and revision.passed is grade.passed
        and revision.score == grade.score
        and _thaw_json(revision.evidence) == _thaw_json(evidence)
        and revision.failure_classification
        == grade.failure_classification
        and revision.flake_classification == grade.flake_classification
        and revision.supersedes_grade_id is None
    )


def _revision_from_row(row: sqlite3.Row) -> GradeRevision:
    run = RunEnvelopeRef(
        run_id=str(row["run_id"]),
        run_envelope_hash=str(row["run_envelope_hash"]),
        frozen_result_hash=str(row["frozen_result_hash"]),
    )
    revision = GradeRevision(
        grade_id=str(row["grade_id"]),
        revision_hash=str(row["revision_hash"]),
        revision_number=int(row["revision_number"]),
        run_envelope=run,
        verifier_id=str(row["verifier_id"]),
        verifier_version=str(row["verifier_version"]),
        verifier_config_hash=str(row["verifier_config_hash"]),
        verifier_implementation_hash=str(row["verifier_implementation_hash"]),
        passed=bool(row["passed"]),
        score=float(row["score"]),
        evidence=json.loads(str(row["evidence_json"])),
        failure_classification=str(row["failure_classification"]),
        flake_classification=str(row["flake_classification"]),
        supersedes_grade_id=(
            str(row["supersedes_grade_id"])
            if row["supersedes_grade_id"] is not None
            else None
        ),
        recorded_at_ms=int(row["recorded_at_ms"]),
    )
    expected_hash = _sha256_json(
        _grade_revision_payload(
            grade_id=revision.grade_id,
            revision_number=revision.revision_number,
            run_envelope=revision.run_envelope,
            verifier_id=revision.verifier_id,
            verifier_version=revision.verifier_version,
            verifier_config_hash=revision.verifier_config_hash,
            verifier_implementation_hash=revision.verifier_implementation_hash,
            passed=revision.passed,
            score=revision.score,
            evidence=revision.evidence,
            failure_classification=revision.failure_classification,
            flake_classification=revision.flake_classification,
            supersedes_grade_id=revision.supersedes_grade_id,
            recorded_at_ms=revision.recorded_at_ms,
        )
    )
    if expected_hash != revision.revision_hash:
        raise GradeIntegrityError(
            f"grade revision hash mismatch for {revision.grade_id}"
        )
    return revision


def _invalidation_from_row(row: sqlite3.Row) -> GradeInvalidation:
    invalidation = GradeInvalidation(
        invalidation_id=str(row["invalidation_id"]),
        invalidation_hash=str(row["invalidation_hash"]),
        grade_id=str(row["grade_id"]),
        grade_revision_hash=str(row["grade_revision_hash"]),
        kind=str(row["kind"]),
        reason=str(row["reason"]),
        replacement_grade_id=(
            str(row["replacement_grade_id"])
            if row["replacement_grade_id"] is not None
            else None
        ),
        replacement_revision_hash=(
            str(row["replacement_revision_hash"])
            if row["replacement_revision_hash"] is not None
            else None
        ),
        recorded_at_ms=int(row["recorded_at_ms"]),
    )
    expected_hash = _sha256_json(
        _grade_invalidation_payload(
            invalidation_id=invalidation.invalidation_id,
            grade_id=invalidation.grade_id,
            grade_revision_hash=invalidation.grade_revision_hash,
            kind=invalidation.kind,
            reason=invalidation.reason,
            replacement_grade_id=invalidation.replacement_grade_id,
            replacement_revision_hash=invalidation.replacement_revision_hash,
            recorded_at_ms=invalidation.recorded_at_ms,
        )
    )
    if expected_hash != invalidation.invalidation_hash:
        raise GradeIntegrityError(
            "grade invalidation hash mismatch for "
            f"{invalidation.invalidation_id}"
        )
    return invalidation


def _terminal_commit_from_row(row: sqlite3.Row) -> GradeTerminalCommit:
    commit = GradeTerminalCommit(
        commit_id=str(row["commit_id"]),
        commit_hash=str(row["commit_hash"]),
        grade_id=str(row["grade_id"]),
        grade_revision_hash=str(row["grade_revision_hash"]),
        experiment_id=str(row["experiment_id"]),
        task_id=str(row["task_id"]),
        arm=str(row["arm"]),
        terminal_state=str(row["terminal_state"]),
        terminal_state_hash=str(row["terminal_state_hash"]),
        recorded_at_ms=int(row["recorded_at_ms"]),
    )
    expected_hash = _sha256_json(
        _grade_terminal_commit_payload(
            commit_id=commit.commit_id,
            grade_id=commit.grade_id,
            grade_revision_hash=commit.grade_revision_hash,
            experiment_id=commit.experiment_id,
            task_id=commit.task_id,
            arm=commit.arm,
            terminal_state=commit.terminal_state,
            terminal_state_hash=commit.terminal_state_hash,
            recorded_at_ms=commit.recorded_at_ms,
        )
    )
    if expected_hash != commit.commit_hash:
        raise GradeIntegrityError(
            f"grade terminal commit hash mismatch for {commit.commit_id}"
        )
    return commit


def _decision_from_row(row: sqlite3.Row) -> GradeDecisionRecord:
    try:
        decision = json.loads(str(row["decision_json"]))
        raw_citations = json.loads(str(row["grade_citations_json"]))
    except json.JSONDecodeError as exc:
        raise GradeIntegrityError(
            "persisted grade-backed decision JSON is invalid"
        ) from exc
    if not isinstance(decision, Mapping) or not isinstance(
        raw_citations,
        list,
    ):
        raise GradeIntegrityError(
            "persisted grade-backed decision shape is invalid"
        )
    citations = tuple(
        DecisionGradeCitation.from_mapping(value)
        for value in raw_citations
        if isinstance(value, Mapping)
    )
    if len(citations) != len(raw_citations):
        raise GradeIntegrityError(
            "persisted grade-backed decision citations are invalid"
        )
    record = GradeDecisionRecord(
        decision_id=str(row["decision_id"]),
        decision_hash=str(row["decision_hash"]),
        decision=decision,
        grade_citations=citations,
        recorded_at_ms=int(row["recorded_at_ms"]),
    )
    expected_hash = _sha256_json({
        "schema_version": GRADE_DECISION_SCHEMA_VERSION,
        "decision_id": record.decision_id,
        "decision": _thaw_json(record.decision),
        "grade_citations": [
            citation.to_dict() for citation in record.grade_citations
        ],
        "recorded_at_ms": record.recorded_at_ms,
    })
    if expected_hash != record.decision_hash:
        raise GradeIntegrityError(
            f"grade-backed decision hash mismatch for {record.decision_id}"
        )
    return record


def _grade_revision_payload(
    *,
    grade_id: str,
    revision_number: int,
    run_envelope: RunEnvelopeRef,
    verifier_id: str,
    verifier_version: str,
    verifier_config_hash: str,
    verifier_implementation_hash: str,
    passed: bool,
    score: float,
    evidence: Mapping[str, Any],
    failure_classification: str,
    flake_classification: str,
    supersedes_grade_id: str | None,
    recorded_at_ms: int,
) -> dict[str, Any]:
    return {
        "schema_version": GRADE_REVISION_SCHEMA_VERSION,
        "grade_id": grade_id,
        "revision_number": revision_number,
        "run_envelope": run_envelope.to_dict(),
        "verifier": {
            "id": verifier_id,
            "version": verifier_version,
            "config_hash": verifier_config_hash,
            "implementation_hash": verifier_implementation_hash,
        },
        "passed": bool(passed),
        "score": float(score),
        "evidence": _thaw_json(evidence),
        "failure_classification": str(failure_classification),
        "flake_classification": str(flake_classification),
        "supersedes_grade_id": supersedes_grade_id,
        "recorded_at_ms": int(recorded_at_ms),
    }


def _grade_invalidation_payload(
    *,
    invalidation_id: str,
    grade_id: str,
    grade_revision_hash: str,
    kind: str,
    reason: str,
    replacement_grade_id: str | None,
    replacement_revision_hash: str | None,
    recorded_at_ms: int,
) -> dict[str, Any]:
    return {
        "schema_version": GRADE_INVALIDATION_SCHEMA_VERSION,
        "invalidation_id": invalidation_id,
        "grade_id": grade_id,
        "grade_revision_hash": grade_revision_hash,
        "kind": kind,
        "reason": reason,
        "replacement_grade_id": replacement_grade_id,
        "replacement_revision_hash": replacement_revision_hash,
        "recorded_at_ms": int(recorded_at_ms),
    }


def _grade_terminal_commit_payload(
    *,
    commit_id: str,
    grade_id: str,
    grade_revision_hash: str,
    experiment_id: str,
    task_id: str,
    arm: str,
    terminal_state: str,
    terminal_state_hash: str,
    recorded_at_ms: int,
) -> dict[str, Any]:
    return {
        "schema_version": GRADE_TERMINAL_COMMIT_SCHEMA_VERSION,
        "commit_id": commit_id,
        "grade_id": grade_id,
        "grade_revision_hash": grade_revision_hash,
        "experiment_id": experiment_id,
        "task_id": task_id,
        "arm": arm,
        "terminal_state": terminal_state,
        "terminal_state_hash": terminal_state_hash,
        "recorded_at_ms": int(recorded_at_ms),
    }


def _require_text(field: str, value: object) -> str:
    if value is None:
        raise GradeValidationError(f"{field} is required")
    text = str(value).strip()
    if not text:
        raise GradeValidationError(f"{field} is required")
    return text


def _require_sha256(field: str, value: object) -> str:
    digest = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise GradeValidationError(
            f"{field} must be a 64-character lowercase SHA-256 hex digest"
        )
    return digest


def _require_score(value: object) -> float:
    if isinstance(value, bool):
        raise GradeValidationError("score must be a finite number")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise GradeValidationError("score must be a finite number") from exc
    if not math.isfinite(score):
        raise GradeValidationError("score must be a finite number")
    return score


def _normalise_json(value: object, *, field: str) -> Any:
    try:
        _validate_json_tree(value, path=field)
        return json.loads(_canonical_json(_thaw_json(value)))
    except (TypeError, ValueError) as exc:
        raise GradeValidationError(
            f"{field} must contain canonical JSON-compatible values"
        ) from exc


def _validate_json_tree(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise GradeValidationError(
                    f"{path} contains a non-string mapping key"
                )
            _validate_json_tree(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_tree(item, path=f"{path}[{index}]")
        return
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise GradeValidationError(
        f"{path} contains a non-JSON value of type {type(value).__name__}"
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({
            str(key): _freeze_json(item)
            for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value
