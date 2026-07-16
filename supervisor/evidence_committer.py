"""Crash-resumable evidence commits for the hermetic Harness v1 tracer.

The committer binds already-frozen execution and grade records into one
authoritative evidence spine:

* immutable GradeBook history is re-read and compared with caller references;
* the trace graph is persisted and reloaded through ``TraceGraphStore``;
* claim artifacts and canonical store exports are written to a CAS;
* one artifact-manifest event is appended to the aggregate run;
* every participating run is anchored by an external signed checkpoint;
* a named tracer projection is rebuilt from the checkpoint-verified stream.

This module is deliberately limited to hermetic, non-operational evidence and
refuses claim levels above L2.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - secure snapshots fail closed below
    fcntl = None  # type: ignore[assignment]

from .evidence_ledger import (
    ArtifactIntegrityError,
    ContentAddressedArtifactStore,
    EVIDENCE_COMMIT_EVENT_KIND,
    EVIDENCE_COMMIT_EVENT_SOURCE,
    LedgerVerification,
    _absolute_no_follow_path,
    _append_only_file_at,
    _directory_tree_fd,
    _open_child_directory,
    _read_regular_file_at,
    canonical_json_bytes,
    rebuild_projection,
    sha256_hex,
    verify_artifact_manifest_attestation,
    verify_event_chain,
)
from .claim_gate import ClaimGate, ClaimGateError
from .experiment_kernel import GradeRevisionRef, SqliteExperimentStore
from .grade_revisions import (
    DecisionGradeCitation,
    GradeBook,
    GradeIntegrityError,
    GradeInvalidation,
    GradeNotFoundError,
    GradeRevision,
    GradeTerminalCommit,
    GradeValidationError,
    RunEnvelopeRef,
)
from .ledger_checkpoints import (
    LedgerCheckpointStore,
    PersistedLedgerCheckpoint,
    TrustedCheckpointPinStore,
    Verifier,
    checkpoint_identity,
    normalize_checkpoint_identity,
    verify_authoritative_event_chain,
)
from .run_registry import validate_run_registration_authority
from .state import State, is_postgres_state_dsn
from .trace_graph import (
    TRACE_GRAPH_LIFECYCLE_SCHEMA_VERSION,
    ClosureResult,
    EdgeType,
    NodeType,
    TraceGraph,
    TraceGraphError,
    TraceGraphStore,
    TraceIdentity,
    TraceLifecycleRevision,
    TraceLifecycleStage,
    TraceNode,
)


EVIDENCE_COMMIT_SCHEMA_VERSION = "harness-evidence-commit/v2"
TRACER_PROJECTION_SCHEMA_VERSION = "harness-tracer-projection/v1"
SQLITE_EXPORT_SCHEMA_VERSION = "harness-sqlite-export/v1"
_RECOVERY_CHECKPOINT_SELECTION_SCHEMA_VERSION = (
    "harness-evidence-recovery-checkpoint-selection/v1"
)
_MAX_RECOVERY_CHECKPOINT_REPLANS = 8
TRACE_LIFECYCLE_PERSISTENCE_SEMANTICS = (
    "post_execution_stage_projection"
)

_PHASES = (
    "initialized",
    "grades_verified",
    "trace_persisted",
    "artifacts_staged",
    "manifest_appended",
    "checkpoints_persisted",
    "authoritatively_verified",
    "complete",
)
_PHASE_INDEX = {phase: index for index, phase in enumerate(_PHASES)}
_REQUIRED_ARTIFACT_ROLES = frozenset(
    {
        "claim_evidence_bundle",
        "claim_report",
        "execution_results",
        "hidden_verifier_result",
        "run_manifest",
        "trace_graph",
    }
)
_RECOGNIZED_PROJECTION_EVENTS = frozenset(
    {
        "tracer.submitted",
        "tracer.matrix.frozen",
        "tracer.assignment.persisted",
        "tracer.execution.joined",
        "tracer.trace.closed",
        "tracer.claim.authorized",
        "tracer.completed",
    }
)


class EvidenceCommitError(RuntimeError):
    """Base error for a durable evidence commit."""


class EvidenceCommitConflict(EvidenceCommitError):
    """The same durable identity was replayed with different content."""


class EvidenceCommitIntegrityError(EvidenceCommitError):
    """Persisted evidence no longer matches its authenticated references."""


@dataclass(frozen=True)
class EvidenceArtifact:
    """One canonical artifact supplied to the committer."""

    role: str
    relative_path: str
    content: bytes
    media_type: str = "application/json"

    def __post_init__(self) -> None:
        role = str(self.role).strip()
        if not role:
            raise ValueError("evidence artifact role is required")
        relative = _safe_relative_path(self.relative_path)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "relative_path", relative.as_posix())
        object.__setattr__(self, "content", bytes(self.content))
        object.__setattr__(self, "media_type", str(self.media_type).strip())
        if not self.media_type:
            raise ValueError("evidence artifact media_type is required")

    def fingerprint(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "relative_path": self.relative_path,
            "sha256": sha256_hex(self.content),
            "size": len(self.content),
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class EvidenceGradeHistory:
    """Exact immutable grade lineage for one frozen execution result."""

    execution_id: str
    run: RunEnvelopeRef
    revisions: tuple[GradeRevision, ...]
    invalidations: tuple[GradeInvalidation, ...]
    terminal_commits: tuple[GradeTerminalCommit, ...] = ()
    source_terminal_commits: tuple[GradeTerminalCommit, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_id", str(self.execution_id))
        object.__setattr__(self, "revisions", tuple(self.revisions))
        object.__setattr__(self, "invalidations", tuple(self.invalidations))
        object.__setattr__(
            self,
            "terminal_commits",
            tuple(self.terminal_commits),
        )
        object.__setattr__(
            self,
            "source_terminal_commits",
            tuple(self.source_terminal_commits),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "run": self.run.to_dict(),
            "revisions": [revision.to_dict() for revision in self.revisions],
            "invalidations": [
                invalidation.to_dict() for invalidation in self.invalidations
            ],
            "terminal_commits": [
                commit.to_dict() for commit in self.terminal_commits
            ],
            "source_terminal_commits": [
                commit.to_dict()
                for commit in self.source_terminal_commits
            ],
        }


class _CommittedGradeBookView(GradeBook):
    """Authoritative GradeBook view pinned to committed grade lineages."""

    def __init__(
        self,
        histories: Sequence[EvidenceGradeHistory],
    ) -> None:
        self._pinned_revisions: dict[str, GradeRevision] = {}
        self._pinned_runs: dict[
            RunEnvelopeRef,
            tuple[GradeRevision, ...],
        ] = {}
        self._pinned_invalidations: dict[
            str,
            tuple[GradeInvalidation, ...],
        ] = {}
        self._pinned_terminal_commits: dict[str, GradeTerminalCommit] = {}
        for history in histories:
            self._pinned_runs[history.run] = tuple(history.revisions)
            for revision in history.revisions:
                self._pinned_revisions[revision.grade_id] = revision
                self._pinned_invalidations.setdefault(revision.grade_id, ())
            for invalidation in history.invalidations:
                self._pinned_invalidations[invalidation.grade_id] = (
                    self._pinned_invalidations.get(
                        invalidation.grade_id,
                        (),
                    )
                    + (invalidation,)
                )
            for commit in history.terminal_commits:
                self._pinned_terminal_commits[commit.grade_id] = commit

    def get_revision(self, grade_id: str) -> GradeRevision:
        revision = self._pinned_revisions.get(str(grade_id))
        if revision is None:
            raise GradeNotFoundError(f"unknown grade revision: {grade_id}")
        return revision

    def list_revisions(
        self,
        run: RunEnvelopeRef,
    ) -> tuple[GradeRevision, ...]:
        return self._pinned_runs.get(run, ())

    def list_invalidations(
        self,
        grade_id: str,
    ) -> tuple[GradeInvalidation, ...]:
        return self._pinned_invalidations.get(str(grade_id), ())

    def get_terminal_commit(
        self,
        grade_id: str,
    ) -> GradeTerminalCommit | None:
        return self._pinned_terminal_commits.get(str(grade_id))


def _trace_graph_is_append_extension(
    *,
    committed: TraceGraph,
    live: TraceGraph,
) -> bool:
    for attribute in ("nodes", "edges", "waivers"):
        committed_records = getattr(committed, attribute)
        live_records = getattr(live, attribute)
        if len(live_records) < len(committed_records):
            return False
        for pinned, observed in zip(committed_records, live_records):
            if canonical_json_bytes(pinned.to_dict()) != (
                canonical_json_bytes(observed.to_dict())
            ):
                return False
    return True


def _manifest_attestation_identity(
    attestation: Mapping[str, Any],
) -> dict[str, Any]:
    predicate = attestation.get("predicate")
    signatures = attestation.get("signatures")
    signing_payload_sha256 = str(
        attestation.get("signing_payload_sha256") or ""
    )
    if (
        not isinstance(predicate, Mapping)
        or not isinstance(signatures, list)
        or not signatures
        or len(signing_payload_sha256) != 64
    ):
        raise EvidenceCommitIntegrityError(
            "artifact manifest attestation identity is invalid"
        )
    identity = {
        "schema_version": str(predicate.get("schema_version") or ""),
        "manifest_hash": str(predicate.get("manifest_hash") or ""),
        "signing_payload_sha256": signing_payload_sha256,
        "signer_provider_id": str(
            predicate.get("signer_provider_id") or ""
        ),
        "signer_key_id": str(predicate.get("signer_key_id") or ""),
        "signer_algorithm": str(
            predicate.get("signer_algorithm") or ""
        ),
        "created_at": predicate.get("created_at"),
        "signatures_sha256": sha256_hex(
            canonical_json_bytes(signatures)
        ),
    }
    if (
        any(
            not str(identity[field]).strip()
            for field in (
                "schema_version",
                "manifest_hash",
                "signer_provider_id",
                "signer_key_id",
                "signer_algorithm",
            )
        )
        or len(identity["manifest_hash"]) != 64
        or type(identity["created_at"]) is not int
        or int(identity["created_at"]) < 0
    ):
        raise EvidenceCommitIntegrityError(
            "artifact manifest attestation identity is invalid"
        )
    return identity


def _trace_lifecycle_identity(
    revisions: Sequence[TraceLifecycleRevision],
) -> dict[str, Any]:
    ordered = tuple(revisions)
    if tuple(revision.stage for revision in ordered) != tuple(
        TraceLifecycleStage
    ):
        raise EvidenceCommitIntegrityError(
            "trace lifecycle does not contain the exact ordered stages"
        )
    return {
        "schema_version": TRACE_GRAPH_LIFECYCLE_SCHEMA_VERSION,
        "persistence_semantics": TRACE_LIFECYCLE_PERSISTENCE_SEMANTICS,
        "pre_execution_attested": False,
        "stages": [revision.stage.value for revision in ordered],
        "revision_hashes": [
            revision.revision_hash for revision in ordered
        ],
        "head_revision_hash": ordered[-1].revision_hash,
    }


@dataclass(frozen=True)
class EvidenceCommitRequest:
    """All immutable inputs to one evidence commit."""

    commit_id: str
    aggregate_run_id: str
    registered_run_ids: tuple[str, ...]
    mode: str
    claim_cap: str
    operational_efficacy_evidence: bool
    subject: Mapping[str, Any]
    grade_histories: tuple[EvidenceGradeHistory, ...]
    trace_graph: TraceGraph
    promotion: TraceIdentity
    closure_time: datetime
    artifacts: tuple[EvidenceArtifact, ...]
    manifest_event_ts: int
    checkpoint_created_at: int

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "schema_version": EVIDENCE_COMMIT_SCHEMA_VERSION,
            "commit_id": self.commit_id,
            "aggregate_run_id": self.aggregate_run_id,
            "registered_run_ids": list(self.registered_run_ids),
            "mode": self.mode,
            "claim_cap": self.claim_cap,
            "operational_efficacy_evidence": (
                self.operational_efficacy_evidence
            ),
            "subject": dict(self.subject),
            "grade_histories": [
                history.to_dict() for history in self.grade_histories
            ],
            "trace_graph_sha256": sha256_hex(
                self.trace_graph.canonical_bytes()
            ),
            "trace_expected_binding": (
                None
                if self.trace_graph.expected_binding is None
                else self.trace_graph.expected_binding.to_dict()
            ),
            "promotion": self.promotion.to_dict(),
            "closure_time": self.closure_time.isoformat(),
            "artifacts": [
                artifact.fingerprint() for artifact in self.artifacts
            ],
            "manifest_event_ts": int(self.manifest_event_ts),
            "checkpoint_created_at": int(self.checkpoint_created_at),
        }


@dataclass(frozen=True)
class EvidenceCommitResult:
    commit_id: str
    request_hash: str
    status: str
    phases: tuple[str, ...]
    artifacts: tuple[Mapping[str, Any], ...]
    artifact_manifest: Mapping[str, Any]
    manifest_event_id: int
    manifest_event_hash: str
    checkpoint_refs: Mapping[str, str]
    ledger_verifications: Mapping[str, LedgerVerification]
    trace_graph: TraceGraph
    trace_closure: ClosureResult
    promotion_trace: tuple[TraceNode, ...]
    projection: Mapping[str, Any]
    projection_sha256: str
    evidence_root: Path
    artifact_manifest_attestation_identity: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class _RecoveryCheckpointPlan:
    run_id: str
    recovery_events: tuple[dict[str, Any], ...]
    current_events: tuple[dict[str, Any], ...]
    local_latest: PersistedLedgerCheckpoint | None
    selected_existing: PersistedLedgerCheckpoint | None


class HmacCheckpointAuthority:
    """Small signer/verifier used by explicitly hermetic evidence commits."""

    algorithm = "hmac-sha256"

    def __init__(self, *, key_id: str, key: bytes) -> None:
        normalized_key_id = str(key_id).strip()
        if not normalized_key_id:
            raise ValueError("checkpoint key_id is required")
        if not key:
            raise ValueError("checkpoint HMAC key is required")
        self.key_id = normalized_key_id
        self._key = bytes(key)

    def sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def verify(
        self,
        payload: bytes,
        signature: Mapping[str, Any],
    ) -> bool:
        if (
            signature.get("key_id") != self.key_id
            or signature.get("algorithm") != self.algorithm
        ):
            return False
        expected = base64.b64encode(self.sign(payload)).decode("ascii")
        return hmac.compare_digest(
            str(signature.get("signature") or ""),
            expected,
        )


def initial_tracer_evidence_projection(run_id: str) -> dict[str, Any]:
    """Return the empty canonical projection for one aggregate tracer run."""
    return {
        "schema_version": TRACER_PROJECTION_SCHEMA_VERSION,
        "aggregate_run_id": str(run_id),
        "mode": None,
        "operational_efficacy_evidence": None,
        "not_executed": [],
        "matrix": [],
        "assignments": {},
        "executions": {},
        "trace": None,
        "claim": None,
        "completion": None,
        "recognized_event_count": 0,
        "recognized_event_hashes": [],
    }


def reduce_tracer_evidence_projection(
    projection: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Reduce aggregate tracer events into a replayable named projection.

    The artifact-manifest event is intentionally not a domain transition. This
    lets the projection itself live inside that manifest without a recursive
    self-hash, while authoritative replay still consumes the complete chain.
    """
    current = dict(projection)
    run_id = str(event.get("run_id") or "")
    if run_id != current.get("aggregate_run_id"):
        raise EvidenceCommitIntegrityError(
            "tracer projection received an event from another run"
        )
    kind = str(event.get("kind") or "")
    if kind not in _RECOGNIZED_PROJECTION_EVENTS:
        return current
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise EvidenceCommitIntegrityError(
            f"tracer projection event {kind!r} has no object payload"
        )

    if kind == "tracer.submitted":
        _set_projection_once(
            current,
            "mode",
            str(payload.get("mode") or ""),
        )
        _set_projection_once(
            current,
            "operational_efficacy_evidence",
            payload.get("operational_efficacy_evidence"),
        )
        current["not_executed"] = list(payload.get("not_executed") or ())
    elif kind == "tracer.matrix.frozen":
        if current["matrix"]:
            raise EvidenceCommitIntegrityError(
                "tracer projection observed duplicate matrix freeze"
            )
        coordinates = payload.get("coordinates")
        if not isinstance(coordinates, list):
            raise EvidenceCommitIntegrityError(
                "tracer matrix projection requires coordinates"
            )
        current["matrix"] = coordinates
    elif kind == "tracer.assignment.persisted":
        key_parts = tuple(
            str(payload.get(field) or "")
            for field in ("experiment_id", "task_id", "runtime_kind")
        )
        if not all(key_parts):
            raise EvidenceCommitIntegrityError(
                "tracer assignment projection lacks canonical identity"
            )
        key = "|".join(key_parts)
        current["assignments"] = dict(current["assignments"])
        _insert_projection_record(
            current["assignments"],
            key,
            {
                field: payload.get(field)
                for field in (
                    "experiment_id",
                    "task_id",
                    "task_family",
                    "runtime_kind",
                    "assignment_id",
                    "order",
                )
            },
        )
    elif kind == "tracer.execution.joined":
        execution_id = str(payload.get("execution_id") or "")
        current["executions"] = dict(current["executions"])
        _insert_projection_record(
            current["executions"],
            execution_id,
            {
                field: payload.get(field)
                for field in (
                    "execution_id",
                    "experiment_id",
                    "task_id",
                    "task_family",
                    "runtime_kind",
                    "arm",
                    "assignment_id",
                    "runtime_run_id",
                    "runtime_session_id",
                    "original_frozen_result_hash",
                    "blinded_frozen_result_hash",
                    "grade_revision_hash",
                )
            },
        )
    elif kind == "tracer.trace.closed":
        _set_projection_once(
            current,
            "trace",
            {
                field: payload.get(field)
                for field in (
                    "status",
                    "node_count",
                    "edge_count",
                    "promotion_trace",
                )
            },
        )
    elif kind == "tracer.claim.authorized":
        _set_projection_once(
            current,
            "claim",
            {
                field: payload.get(field)
                for field in (
                    "max_claim_level",
                    "l3_refusal",
                    "operational_efficacy_evidence",
                    "improvement_claim_allowed",
                )
            },
        )
    elif kind == "tracer.completed":
        _set_projection_once(
            current,
            "completion",
            {
                field: payload.get(field)
                for field in (
                    "execution_count",
                    "claim_cap",
                    "mode",
                    "external_provider_calls",
                )
            },
        )

    event_hash = str(event.get("event_hash") or "")
    if len(event_hash) != 64:
        raise EvidenceCommitIntegrityError(
            f"tracer projection event {kind!r} lacks an event hash"
        )
    current["recognized_event_count"] = current["recognized_event_count"] + 1
    current["recognized_event_hashes"] = [
        *current["recognized_event_hashes"],
        event_hash,
    ]
    return current


class EvidenceCommitter:
    """Deep module that owns durable evidence commit ordering and recovery."""

    def __init__(
        self,
        *,
        root: str | Path,
        state: State,
        experiment_db_path: str | Path,
        gradebook_path: str | Path,
        trace_store_path: str | Path,
        signer: Any,
        verifier: Verifier,
        trusted_checkpoint_pins: TrustedCheckpointPinStore | None = None,
        phase_observer: Callable[[str], None] | None = None,
    ) -> None:
        self.root = _absolute_no_follow_path(root)
        with _directory_tree_fd(
            self.root,
            create=True,
            error_type=EvidenceCommitIntegrityError,
            label="evidence root",
        ):
            pass
        self.state = state
        self._event_write_capability = (
            self.state._bind_evidence_commit_writer(self)
        )
        self.experiment_db_path = _absolute_no_follow_path(
            experiment_db_path
        )
        self.gradebook_path = _absolute_no_follow_path(gradebook_path)
        self.trace_store_path = _absolute_no_follow_path(trace_store_path)
        self.signer = signer
        self.verifier = verifier
        self.trusted_checkpoint_pins = trusted_checkpoint_pins
        self.phase_observer = phase_observer
        self.artifact_store = ContentAddressedArtifactStore(self.root / "cas")
        self.checkpoint_store = LedgerCheckpointStore(
            self.root / "checkpoints"
        )
        self.outbox_path = self.root / "evidence-commits.db"
        self._lock = threading.RLock()
        self._initialise_outbox()

    def commit(self, request: EvidenceCommitRequest) -> EvidenceCommitResult:
        """Commit or resume one exact evidence request, failing closed on drift."""
        self._validate_request(request)
        request_json = canonical_json_bytes(
            request.fingerprint_payload()
        ).decode("utf-8")
        request_hash = sha256_hex(request_json.encode("utf-8"))
        row = self._begin_or_resume(
            request=request,
            request_json=request_json,
            request_hash=request_hash,
        )
        was_complete = str(row["status"]) == "complete"
        try:
            if was_complete:
                return self._load_completed_result(
                    request=request,
                    request_hash=request_hash,
                    row=row,
                )
            if row["materialization_json"] is not None:
                return self._resume_materialized_commit(
                    request=request,
                    request_hash=request_hash,
                    row=row,
                )
            precommit_heads = _decode_json_object(
                row["precommit_heads_json"],
                field="precommit_heads_json",
            )
            self._persist_planning_projection(
                request,
                prior_phase=str(row["phase"]),
            )
            histories = self._verify_grade_histories(request)
            self._advance(
                request.commit_id,
                "grades_verified",
                {
                    "grade_history_count": len(histories),
                    "grade_revision_count": sum(
                        len(history.revisions) for history in histories
                    ),
                },
            )

            (
                graph,
                closure,
                promotion_trace,
                trace_lifecycle_identity,
            ) = self._persist_trace(request, histories)
            self._advance(
                request.commit_id,
                "trace_persisted",
                {
                    "trace_sha256": sha256_hex(graph.canonical_bytes()),
                    "node_count": len(graph.nodes),
                    "edge_count": len(graph.edges),
                    "closure_status": closure.to_dict()["status"],
                    "trace_lifecycle": trace_lifecycle_identity,
                },
            )

            materialization = self._load_or_stage_artifacts(
                request=request,
                request_hash=request_hash,
                histories=histories,
                graph=graph,
                closure=closure,
                precommit_heads=precommit_heads,
                trace_lifecycle_identity=trace_lifecycle_identity,
            )
            manifest_event = self._append_or_verify_manifest_event(
                request=request,
                request_hash=request_hash,
                materialization=materialization,
                precommit_heads=precommit_heads,
            )
            checkpoint_streams = self._verify_frozen_checkpoint_streams(
                request=request,
                precommit_heads=precommit_heads,
                manifest_event=manifest_event,
            )
            (
                checkpoint_refs,
                trusted_checkpoint_pins,
            ) = self._persist_checkpoint_phase(
                request,
                checkpoint_streams,
            )
            verifications, projection = self._verify_authoritatively(
                request=request,
                materialization=materialization,
                graph=graph,
                closure=closure,
                manifest_event=manifest_event,
                precommit_heads=precommit_heads,
                event_streams=checkpoint_streams,
                trusted_checkpoint_pins=trusted_checkpoint_pins,
            )
            result_summary = {
                "schema_version": EVIDENCE_COMMIT_SCHEMA_VERSION,
                "request_hash": request_hash,
                "artifact_manifest_hash": materialization[
                    "artifact_manifest"
                ]["manifest_hash"],
                "artifact_manifest_attestation": materialization[
                    "artifact_manifest_attestation_identity"
                ],
                "trace_lifecycle": materialization["trace_lifecycle"],
                "manifest_event_id": manifest_event["event_id"],
                "manifest_event_hash": manifest_event["event_hash"],
                "checkpoint_refs": checkpoint_refs,
                "trusted_checkpoint_pins": trusted_checkpoint_pins,
                "projection_sha256": materialization["projection_sha256"],
            }
            self._complete(request.commit_id, result_summary)
            return self._result(
                request=request,
                request_hash=request_hash,
                materialization=materialization,
                manifest_event=manifest_event,
                checkpoint_refs=checkpoint_refs,
                verifications=verifications,
                graph=graph,
                closure=closure,
                promotion_trace=promotion_trace,
                projection=projection,
            )
        except Exception as exc:
            if not was_complete:
                self._mark_failed(request.commit_id, exc)
            raise

    def _resume_materialized_commit(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        row: sqlite3.Row,
    ) -> EvidenceCommitResult:
        """Resume from the immutable cut established by materialization."""
        materialization = _decode_json_object(
            row["materialization_json"],
            field="materialization_json",
        )
        precommit_heads = _decode_json_object(
            row["precommit_heads_json"],
            field="precommit_heads_json",
        )
        graph, closure, promotion_trace = (
            self._load_materialized_authority(
                request=request,
                request_hash=request_hash,
                materialization=materialization,
            )
        )
        manifest_event = self._append_or_verify_manifest_event(
            request=request,
            request_hash=request_hash,
            materialization=materialization,
            precommit_heads=precommit_heads,
            allow_append_suffix=True,
        )
        checkpoint_detail = self._load_phase_detail(
            request.commit_id,
            "checkpoints_persisted",
        )
        checkpoint_streams = self._verify_recovery_checkpoint_streams(
            request=request,
            precommit_heads=precommit_heads,
            manifest_event=manifest_event,
        )
        if checkpoint_detail is None:
            (
                checkpoint_refs,
                trusted_checkpoint_pins,
                checkpoint_streams,
            ) = self._persist_recovery_checkpoint_phase(
                request,
                checkpoint_streams,
            )
        else:
            (
                checkpoint_refs,
                trusted_checkpoint_pins,
                checkpoint_streams,
            ) = self._load_checkpointed_recovery(
                request=request,
                detail=checkpoint_detail,
                expected_streams=checkpoint_streams,
            )
        require_local_latest = False
        verifications, projection = self._verify_authoritatively(
            request=request,
            materialization=materialization,
            graph=graph,
            closure=closure,
            manifest_event=manifest_event,
            precommit_heads=precommit_heads,
            event_streams=checkpoint_streams,
            trusted_checkpoint_pins=trusted_checkpoint_pins,
            require_local_latest=require_local_latest,
        )
        result_summary = {
            "schema_version": EVIDENCE_COMMIT_SCHEMA_VERSION,
            "request_hash": request_hash,
            "artifact_manifest_hash": materialization[
                "artifact_manifest"
            ]["manifest_hash"],
            "artifact_manifest_attestation": materialization[
                "artifact_manifest_attestation_identity"
            ],
            "trace_lifecycle": materialization["trace_lifecycle"],
            "manifest_event_id": manifest_event["event_id"],
            "manifest_event_hash": manifest_event["event_hash"],
            "checkpoint_refs": checkpoint_refs,
            "trusted_checkpoint_pins": trusted_checkpoint_pins,
            "projection_sha256": materialization["projection_sha256"],
        }
        self._complete(request.commit_id, result_summary)
        return self._result(
            request=request,
            request_hash=request_hash,
            materialization=materialization,
            manifest_event=manifest_event,
            checkpoint_refs=checkpoint_refs,
            verifications=verifications,
            graph=graph,
            closure=closure,
            promotion_trace=promotion_trace,
            projection=projection,
        )

    def _load_completed_result(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        row: sqlite3.Row,
    ) -> EvidenceCommitResult:
        """Replay a completed commit from its pinned cut, not the live tail."""
        materialization = _decode_json_object(
            row["materialization_json"],
            field="materialization_json",
        )
        result_summary = _decode_json_object(
            row["result_json"],
            field="result_json",
        )
        graph, closure, promotion_trace = (
            self._load_materialized_authority(
                request=request,
                request_hash=request_hash,
                materialization=materialization,
            )
        )
        materialized_attestation = materialization.get(
            "artifact_manifest_attestation_identity"
        )
        result_has_attestation = (
            "artifact_manifest_attestation" in result_summary
        )
        attestation_mismatch = (
            result_has_attestation
            if materialized_attestation is None
            else (
                not result_has_attestation
                or canonical_json_bytes(
                    result_summary.get("artifact_manifest_attestation")
                )
                != canonical_json_bytes(materialized_attestation)
            )
        )
        materialized_lifecycle = materialization.get("trace_lifecycle")
        result_has_lifecycle = "trace_lifecycle" in result_summary
        lifecycle_mismatch = (
            result_has_lifecycle
            if materialized_lifecycle is None
            else (
                not result_has_lifecycle
                or canonical_json_bytes(
                    result_summary.get("trace_lifecycle")
                )
                != canonical_json_bytes(materialized_lifecycle)
            )
        )
        if (
            result_summary.get("schema_version")
            != EVIDENCE_COMMIT_SCHEMA_VERSION
            or result_summary.get("request_hash") != request_hash
            or result_summary.get("artifact_manifest_hash")
            != materialization["artifact_manifest"]["manifest_hash"]
            or attestation_mismatch
            or lifecycle_mismatch
            or result_summary.get("projection_sha256")
            != materialization["projection_sha256"]
        ):
            raise EvidenceCommitIntegrityError(
                "completed evidence result schema version or authority does "
                "not match its pinned materialization"
            )
        trusted_checkpoint_pins = self._checkpoint_pin_mapping(
            result_summary.get("trusted_checkpoint_pins"),
            run_ids=request.registered_run_ids,
        )
        trusted_checkpoint_pins = self._load_trusted_checkpoint_pins(
            trusted_checkpoint_pins
        )
        trusted_latest_checkpoint_pins = (
            self._load_latest_trusted_checkpoint_pins(
                run_ids=request.registered_run_ids,
                committed=trusted_checkpoint_pins,
            )
        )
        checkpoint_refs = self._checkpoint_ref_mapping(
            result_summary.get("checkpoint_refs"),
            run_ids=request.registered_run_ids,
        )
        expected_refs = {
            run_id: str(identity["external_anchor_ref"])
            for run_id, identity in trusted_checkpoint_pins.items()
        }
        if checkpoint_refs != expected_refs:
            raise EvidenceCommitIntegrityError(
                "completed evidence checkpoint refs differ from trusted pins"
            )
        self._verify_trusted_latest_checkpoint_cuts(
            trusted_latest_checkpoint_pins
        )
        event_streams = self._read_pinned_event_streams(
            trusted_checkpoint_pins
        )
        manifest_event = self._find_manifest_event(request)
        if manifest_event is None:
            raise EvidenceCommitIntegrityError(
                "completed evidence manifest event disappeared"
            )
        if (
            int(manifest_event["event_id"])
            != int(result_summary.get("manifest_event_id"))
            or str(manifest_event["event_hash"])
            != str(result_summary.get("manifest_event_hash"))
        ):
            raise EvidenceCommitIntegrityError(
                "completed evidence manifest event differs from pinned result"
            )
        precommit_heads = _decode_json_object(
            row["precommit_heads_json"],
            field="precommit_heads_json",
        )
        self._verify_manifest_event(
            request=request,
            request_hash=request_hash,
            event=manifest_event,
            materialization=materialization,
            precommit_heads=precommit_heads,
            require_frozen_heads=False,
        )
        verifications, projection = self._verify_authoritatively(
            request=request,
            materialization=materialization,
            graph=graph,
            closure=closure,
            manifest_event=manifest_event,
            precommit_heads=precommit_heads,
            event_streams=event_streams,
            trusted_checkpoint_pins=trusted_checkpoint_pins,
            require_local_latest=False,
            record_phase=False,
        )
        return self._result(
            request=request,
            request_hash=request_hash,
            materialization=materialization,
            manifest_event=manifest_event,
            checkpoint_refs=checkpoint_refs,
            verifications=verifications,
            graph=graph,
            closure=closure,
            promotion_trace=promotion_trace,
            projection=projection,
        )

    def _load_materialized_authority(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        materialization: Mapping[str, Any],
    ) -> tuple[TraceGraph, ClosureResult, tuple[TraceNode, ...]]:
        """Verify the immutable grade and trace prefixes bound into artifacts."""
        histories = self._verify_committed_grade_histories(request)
        try:
            with TraceGraphStore(self.trace_store_path) as store:
                lifecycle = store.list_lifecycle_revisions()
                if tuple(
                    revision.stage for revision in lifecycle
                ) != tuple(TraceLifecycleStage):
                    raise EvidenceCommitIntegrityError(
                        "completed evidence lacks the exact trace lifecycle"
                    )
                trace_lifecycle_identity = _trace_lifecycle_identity(
                    lifecycle
                )
                committed_graph = store.load_lifecycle_revision(
                    TraceLifecycleStage.DECISION
                )
                live_graph = store.load()
        except (OSError, sqlite3.DatabaseError, TraceGraphError) as exc:
            raise EvidenceCommitIntegrityError(
                "completed evidence persisted trace graph differs or is "
                "unreadable"
            ) from exc
        graph = request.trace_graph
        if (
            (
                committed_graph is not None
                and committed_graph.canonical_bytes()
                != graph.canonical_bytes()
            )
            or not _trace_graph_is_append_extension(
                committed=graph,
                live=live_graph,
            )
        ):
            raise EvidenceCommitIntegrityError(
                "completed evidence persisted trace graph differs from "
                "the immutable request"
            )
        committed_gradebook = _CommittedGradeBookView(histories)
        closure = graph.validate_closure(
            now=request.closure_time,
            expected_binding=request.trace_graph.expected_binding,
            decision_grade_validator=committed_gradebook,
        )
        if not closure.ok:
            raise EvidenceCommitIntegrityError(
                "completed evidence trace graph no longer closes"
            )
        self._validate_trace_grade_links(
            graph,
            histories,
        )
        self._validate_trace_grade_decisions(
            graph,
            request.promotion,
            grade_authority=committed_gradebook,
        )
        promotion_trace = graph.promotion_trace(request.promotion)
        self._verify_materialization(
            request=request,
            request_hash=request_hash,
            materialization=materialization,
            graph=graph,
            closure=closure,
            trace_lifecycle_identity=trace_lifecycle_identity,
        )
        return graph, closure, promotion_trace

    def _result(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        materialization: Mapping[str, Any],
        manifest_event: Mapping[str, Any],
        checkpoint_refs: Mapping[str, str],
        verifications: Mapping[str, LedgerVerification],
        graph: TraceGraph,
        closure: ClosureResult,
        promotion_trace: tuple[TraceNode, ...],
        projection: Mapping[str, Any],
    ) -> EvidenceCommitResult:
        raw_attestation_identity = materialization.get(
            "artifact_manifest_attestation_identity"
        )
        attestation_identity = (
            dict(raw_attestation_identity)
            if isinstance(raw_attestation_identity, Mapping)
            else None
        )
        return EvidenceCommitResult(
            commit_id=request.commit_id,
            request_hash=request_hash,
            status="complete",
            phases=self._phase_history(request.commit_id),
            artifacts=tuple(materialization["artifacts"]),
            artifact_manifest=materialization["artifact_manifest"],
            manifest_event_id=int(manifest_event["event_id"]),
            manifest_event_hash=str(manifest_event["event_hash"]),
            checkpoint_refs=dict(checkpoint_refs),
            ledger_verifications=dict(verifications),
            trace_graph=graph,
            trace_closure=closure,
            promotion_trace=promotion_trace,
            projection=dict(projection),
            projection_sha256=str(materialization["projection_sha256"]),
            evidence_root=self.root,
            artifact_manifest_attestation_identity=attestation_identity,
        )

    def _validate_request(self, request: EvidenceCommitRequest) -> None:
        if not str(request.commit_id).strip():
            raise ValueError("evidence commit_id is required")
        if not str(request.aggregate_run_id).strip():
            raise ValueError("aggregate_run_id is required")
        if request.mode != "hermetic":
            raise EvidenceCommitConflict(
                "evidence committer only accepts explicitly hermetic input"
            )
        if request.operational_efficacy_evidence is not False:
            raise EvidenceCommitConflict(
                "hermetic evidence cannot claim operational efficacy"
            )
        if request.claim_cap not in {"L0", "L1", "L2"}:
            raise EvidenceCommitConflict(
                "hermetic evidence commits are capped at L2"
            )
        run_ids = tuple(str(run_id) for run_id in request.registered_run_ids)
        if (
            not run_ids
            or len(set(run_ids)) != len(run_ids)
            or request.aggregate_run_id not in run_ids
        ):
            raise ValueError(
                "registered_run_ids must be unique and include aggregate_run_id"
            )
        if not request.grade_histories:
            raise ValueError("at least one grade history is required")
        execution_ids = [
            history.execution_id for history in request.grade_histories
        ]
        if len(set(execution_ids)) != len(execution_ids):
            raise ValueError("grade history execution_ids must be unique")
        roles = [artifact.role for artifact in request.artifacts]
        paths = [artifact.relative_path for artifact in request.artifacts]
        if len(set(roles)) != len(roles):
            raise ValueError("evidence artifact roles must be unique")
        if len(set(paths)) != len(paths):
            raise ValueError("evidence artifact paths must be unique")
        missing = _REQUIRED_ARTIFACT_ROLES - set(roles)
        if missing:
            raise ValueError(
                "evidence request lacks required artifacts: "
                + ", ".join(sorted(missing))
            )
        if request.closure_time.tzinfo is None:
            raise ValueError("closure_time must be timezone-aware")
        self._validate_claim_artifact(request)
        if (
            self.trusted_checkpoint_pins is None
            or not callable(
                getattr(self.trusted_checkpoint_pins, "pin", None)
            )
            or not callable(
                getattr(self.trusted_checkpoint_pins, "get", None)
            )
            or not callable(
                getattr(self.trusted_checkpoint_pins, "latest", None)
            )
        ):
            raise EvidenceCommitIntegrityError(
                "rollback-resistant evidence commits require an explicit "
                "trusted checkpoint pin store"
            )

    def _validate_claim_artifact(
        self,
        request: EvidenceCommitRequest,
    ) -> None:
        report_artifact = next(
            item for item in request.artifacts if item.role == "claim_report"
        )
        bundle_artifact = next(
            item
            for item in request.artifacts
            if item.role == "claim_evidence_bundle"
        )
        try:
            report = json.loads(report_artifact.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("claim_report artifact is not valid JSON") from exc
        if not isinstance(report, Mapping):
            raise ValueError("claim_report artifact must be a JSON object")
        try:
            evidence_bundle = json.loads(
                bundle_artifact.content.decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "claim_evidence_bundle artifact is not valid JSON"
            ) from exc
        if not isinstance(evidence_bundle, Mapping):
            raise ValueError(
                "claim_evidence_bundle artifact must be a JSON object"
            )
        artifact_bytes = {
            item.relative_path: item.content for item in request.artifacts
        }
        try:
            derived_level = ClaimGate.validate_derived_report(
                report,
                evidence_bundle,
                evidence_resolver=artifact_bytes.get,
            )
        except ClaimGateError as exc:
            raise EvidenceCommitConflict(
                "claim report is not authorized by its bound evidence bundle"
            ) from exc
        observed_cap = (
            None if derived_level is None else derived_level.value
        )
        if observed_cap != request.claim_cap:
            raise EvidenceCommitConflict(
                "ClaimGate-derived report cap does not match evidence commit cap"
            )
        if (
            report.get("operational_efficacy_evidence") is not False
            or report.get("improvement_claim_allowed") is not False
            or report.get("powered_improvement_claim_allowed") is not False
        ):
            raise EvidenceCommitConflict(
                "hermetic claim report contains an operational improvement claim"
            )

    def _initialise_outbox(self) -> None:
        with sqlite3.connect(self.outbox_path) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS evidence_commits (
                  commit_id TEXT PRIMARY KEY,
                  request_hash TEXT NOT NULL,
                  request_json TEXT NOT NULL,
                  status TEXT NOT NULL,
                  phase TEXT NOT NULL,
                  precommit_heads_json TEXT NOT NULL,
                  materialization_json TEXT,
                  result_json TEXT,
                  error_json TEXT,
                  created_at_ms INTEGER NOT NULL,
                  updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_commit_phases (
                  phase_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  commit_id TEXT NOT NULL,
                  phase TEXT NOT NULL,
                  detail_json TEXT NOT NULL,
                  recorded_at_ms INTEGER NOT NULL,
                  UNIQUE(commit_id, phase),
                  FOREIGN KEY(commit_id)
                    REFERENCES evidence_commits(commit_id)
                );
                """
            )

    def _begin_or_resume(
        self,
        *,
        request: EvidenceCommitRequest,
        request_json: str,
        request_hash: str,
    ) -> sqlite3.Row:
        with self._lock, self._outbox_connection() as conn:
            _begin_outbox_transaction(conn)
            row = conn.execute(
                "SELECT * FROM evidence_commits WHERE commit_id=?",
                (request.commit_id,),
            ).fetchone()
            if row is not None:
                if (
                    str(row["request_hash"]) != request_hash
                    or str(row["request_json"]) != request_json
                ):
                    conn.rollback()
                    raise EvidenceCommitConflict(
                        "evidence commit_id already exists with different input"
                    )
                conn.execute(
                    """
                    UPDATE evidence_commits
                       SET status=CASE
                             WHEN status='complete' THEN status
                             ELSE 'in_progress'
                           END,
                           error_json=NULL,
                           updated_at_ms=?
                     WHERE commit_id=?
                    """,
                    (_now_ms(), request.commit_id),
                )
                conn.commit()
                refreshed = conn.execute(
                    "SELECT * FROM evidence_commits WHERE commit_id=?",
                    (request.commit_id,),
                ).fetchone()
                assert refreshed is not None
                return refreshed

            precommit_heads = self._capture_heads(
                request.registered_run_ids
            )
            now = _now_ms()
            detail = {
                "request_hash": request_hash,
                "registered_run_ids": list(request.registered_run_ids),
                "precommit_heads": precommit_heads,
            }
            conn.execute(
                """
                INSERT INTO evidence_commits(
                  commit_id, request_hash, request_json, status, phase,
                  precommit_heads_json, materialization_json, result_json,
                  error_json, created_at_ms, updated_at_ms
                ) VALUES(?, ?, ?, 'in_progress', 'initialized', ?, NULL,
                         NULL, NULL, ?, ?)
                """,
                (
                    request.commit_id,
                    request_hash,
                    request_json,
                    canonical_json_bytes(precommit_heads).decode("utf-8"),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO evidence_commit_phases(
                  commit_id, phase, detail_json, recorded_at_ms
                ) VALUES(?, 'initialized', ?, ?)
                """,
                (
                    request.commit_id,
                    canonical_json_bytes(detail).decode("utf-8"),
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM evidence_commits WHERE commit_id=?",
                (request.commit_id,),
            ).fetchone()
            assert row is not None
        self._observe_phase("initialized")
        return row

    def _verify_grade_histories(
        self,
        request: EvidenceCommitRequest,
    ) -> tuple[EvidenceGradeHistory, ...]:
        verified: list[EvidenceGradeHistory] = []
        terminal_events = self._load_terminal_arm_events()
        registered = set(request.registered_run_ids)
        for run_id in registered:
            self._validated_state_registration(run_id)
        with (
            GradeBook(self.experiment_db_path) as authority_gradebook,
            GradeBook(self.gradebook_path) as gradebook,
        ):
            for expected in request.grade_histories:
                if not expected.terminal_commits:
                    raise EvidenceCommitIntegrityError(
                        "EvidenceGradeHistory terminal_commits are required "
                        f"for execution {expected.execution_id}"
                    )
                if not expected.source_terminal_commits:
                    raise EvidenceCommitIntegrityError(
                        "EvidenceGradeHistory source_terminal_commits are "
                        "required for execution "
                        f"{expected.execution_id}"
                    )
                self._validate_terminal_commit_coverage(expected)
                self._validate_source_terminal_commit_coverage(expected)
                if expected.run.run_id not in registered:
                    raise EvidenceCommitIntegrityError(
                        "GradeBook history references an unregistered run: "
                        f"{expected.run.run_id}"
                    )
                revisions = gradebook.list_revisions(expected.run)
                invalidations = tuple(
                    invalidation
                    for revision in revisions
                    for invalidation in gradebook.list_invalidations(
                        revision.grade_id
                    )
                )
                try:
                    terminal_commits = tuple(
                        commit
                        for revision in revisions
                        if (
                            commit := gradebook.get_terminal_commit(
                                revision.grade_id
                            )
                        )
                        is not None
                    )
                except (GradeIntegrityError, GradeValidationError) as exc:
                    raise EvidenceCommitIntegrityError(
                        "GradeBook terminal grade authority is invalid for "
                        f"execution {expected.execution_id}"
                    ) from exc
                published_history = EvidenceGradeHistory(
                    execution_id=expected.execution_id,
                    run=expected.run,
                    revisions=revisions,
                    invalidations=invalidations,
                    terminal_commits=terminal_commits,
                )
                self._validate_terminal_commit_coverage(published_history)
                if (
                    not revisions
                    or revisions[-1].run_envelope != expected.run
                ):
                    raise EvidenceCommitIntegrityError(
                        "GradeBook history has no current revision for "
                        f"execution {expected.execution_id}"
                    )
                source_terminal_commits = (
                    self._verify_terminal_grade_authority(
                        history=published_history,
                        terminal_events=terminal_events,
                        authority_gradebook=authority_gradebook,
                    )
                )
                actual = EvidenceGradeHistory(
                    execution_id=expected.execution_id,
                    run=expected.run,
                    revisions=revisions,
                    invalidations=invalidations,
                    terminal_commits=terminal_commits,
                    source_terminal_commits=source_terminal_commits,
                )
                self._validate_source_terminal_commit_coverage(actual)
                if canonical_json_bytes(
                    [
                        commit.to_dict()
                        for commit in actual.source_terminal_commits
                    ]
                ) != canonical_json_bytes(
                    [
                        commit.to_dict()
                        for commit in expected.source_terminal_commits
                    ]
                ):
                    raise EvidenceCommitIntegrityError(
                        "source terminal authority does not match the "
                        "immutable evidence request for execution "
                        f"{expected.execution_id}"
                    )
                if canonical_json_bytes(actual.to_dict()) != (
                    canonical_json_bytes(expected.to_dict())
                ):
                    raise EvidenceCommitIntegrityError(
                        "GradeBook history does not match the evidence request "
                        f"for execution {expected.execution_id}"
                    )
                verified.append(actual)
        return tuple(verified)

    def _verify_committed_grade_histories(
        self,
        request: EvidenceCommitRequest,
    ) -> tuple[EvidenceGradeHistory, ...]:
        """Require every live lineage to be an append-extension of the commit."""
        terminal_events = self._load_terminal_arm_events()
        registered = set(request.registered_run_ids)
        for run_id in registered:
            self._validated_state_registration(run_id)
        with (
            GradeBook(self.experiment_db_path) as authority_gradebook,
            GradeBook(self.gradebook_path) as gradebook,
        ):
            for expected in request.grade_histories:
                if not expected.terminal_commits:
                    raise EvidenceCommitIntegrityError(
                        "EvidenceGradeHistory terminal_commits are required "
                        f"for execution {expected.execution_id}"
                    )
                if not expected.source_terminal_commits:
                    raise EvidenceCommitIntegrityError(
                        "EvidenceGradeHistory source_terminal_commits are "
                        "required for execution "
                        f"{expected.execution_id}"
                    )
                self._validate_terminal_commit_coverage(expected)
                self._validate_source_terminal_commit_coverage(expected)
                if expected.run.run_id not in registered:
                    raise EvidenceCommitIntegrityError(
                        "GradeBook history references an unregistered run: "
                        f"{expected.run.run_id}"
                    )
                if (
                    not expected.revisions
                    or expected.revisions[-1].run_envelope != expected.run
                ):
                    raise EvidenceCommitIntegrityError(
                        "GradeBook history has no current revision for "
                        f"execution {expected.execution_id}"
                    )
                self._verify_committed_lineage_prefix(
                    expected,
                    gradebook=gradebook,
                )
                source_terminal_commits = (
                    self._verify_terminal_grade_authority(
                        history=expected,
                        terminal_events=terminal_events,
                        authority_gradebook=authority_gradebook,
                    )
                )
                if canonical_json_bytes(
                    [
                        commit.to_dict()
                        for commit in source_terminal_commits
                    ]
                ) != canonical_json_bytes(
                    [
                        commit.to_dict()
                        for commit in expected.source_terminal_commits
                    ]
                ):
                    raise EvidenceCommitIntegrityError(
                        "source terminal authority does not match the "
                        "immutable evidence request for execution "
                        f"{expected.execution_id}"
                    )
        return tuple(request.grade_histories)

    def _verify_committed_lineage_prefix(
        self,
        expected: EvidenceGradeHistory,
        *,
        gradebook: GradeBook,
    ) -> None:
        def mismatch() -> EvidenceCommitIntegrityError:
            return EvidenceCommitIntegrityError(
                "GradeBook history does not match the evidence request "
                f"for execution {expected.execution_id}"
            )

        live_revisions = gradebook.list_revisions(expected.run)
        if len(live_revisions) < len(expected.revisions):
            raise mismatch()
        for pinned, observed in zip(expected.revisions, live_revisions):
            if canonical_json_bytes(pinned.to_dict()) != (
                canonical_json_bytes(observed.to_dict())
            ):
                raise mismatch()
        committed_grade_ids = {
            revision.grade_id for revision in expected.revisions
        }
        pinned_invalidations: dict[str, list[GradeInvalidation]] = {}
        for invalidation in expected.invalidations:
            if invalidation.grade_id not in committed_grade_ids:
                raise mismatch()
            pinned_invalidations.setdefault(
                invalidation.grade_id,
                [],
            ).append(invalidation)
        pinned_terminal_commits = {
            commit.grade_id: commit
            for commit in expected.terminal_commits
        }
        for revision in expected.revisions:
            try:
                live_invalidations = gradebook.list_invalidations(
                    revision.grade_id
                )
                live_commit = gradebook.get_terminal_commit(
                    revision.grade_id
                )
            except (GradeIntegrityError, GradeValidationError) as exc:
                raise EvidenceCommitIntegrityError(
                    "GradeBook terminal grade authority is invalid for "
                    f"execution {expected.execution_id}"
                ) from exc
            pinned = pinned_invalidations.get(revision.grade_id, [])
            if len(live_invalidations) < len(pinned):
                raise mismatch()
            for pinned_record, observed in zip(pinned, live_invalidations):
                if canonical_json_bytes(pinned_record.to_dict()) != (
                    canonical_json_bytes(observed.to_dict())
                ):
                    raise mismatch()
            pinned_commit = pinned_terminal_commits[revision.grade_id]
            if live_commit is None or canonical_json_bytes(
                live_commit.to_dict()
            ) != canonical_json_bytes(pinned_commit.to_dict()):
                raise mismatch()

    def _validate_terminal_commit_coverage(
        self,
        history: EvidenceGradeHistory,
    ) -> None:
        revision_ids = [
            revision.grade_id for revision in history.revisions
        ]
        terminal_grade_ids = [
            commit.grade_id for commit in history.terminal_commits
        ]
        if (
            len(terminal_grade_ids) != len(revision_ids)
            or len(set(terminal_grade_ids)) != len(terminal_grade_ids)
            or set(terminal_grade_ids) != set(revision_ids)
        ):
            raise EvidenceCommitIntegrityError(
                "EvidenceGradeHistory requires exactly one terminal commit "
                "per published revision with no missing, extra, or duplicate "
                f"grade IDs for execution {history.execution_id}"
            )

    def _validate_source_terminal_commit_coverage(
        self,
        history: EvidenceGradeHistory,
    ) -> None:
        source_grade_ids = [
            commit.grade_id
            for commit in history.source_terminal_commits
        ]
        if (
            len(source_grade_ids) != 1
            or len(set(source_grade_ids)) != len(source_grade_ids)
        ):
            raise EvidenceCommitIntegrityError(
                "EvidenceGradeHistory requires exactly one immutable source "
                "terminal commit for its terminal arm event for execution "
                f"{history.execution_id}"
            )

    def _load_terminal_arm_events(
        self,
    ) -> tuple[Mapping[str, Any], ...]:
        try:
            store = SqliteExperimentStore(self.experiment_db_path)
            return store.list_terminal_arm_events()
        except (OSError, sqlite3.DatabaseError, ValueError) as exc:
            raise EvidenceCommitIntegrityError(
                "experiment terminal arm authority is unreadable"
            ) from exc

    def _verify_terminal_grade_authority(
        self,
        *,
        history: EvidenceGradeHistory,
        terminal_events: tuple[Mapping[str, Any], ...],
        authority_gradebook: GradeBook,
    ) -> tuple[GradeTerminalCommit, ...]:
        revisions_by_id = {
            revision.grade_id: revision for revision in history.revisions
        }
        resolved_events: dict[str, Mapping[str, Any]] = {}
        for commit in history.terminal_commits:
            revision = revisions_by_id.get(commit.grade_id)
            if (
                revision is None
                or commit.grade_revision_hash != revision.revision_hash
            ):
                raise EvidenceCommitIntegrityError(
                    "published terminal grade authority does not match its "
                    "grade revision for execution "
                    f"{history.execution_id}: {commit.grade_id}"
                )
            matching_events = [
                event
                for event in terminal_events
                if (
                    str(event.get("experiment_id") or "")
                    == commit.experiment_id
                    and str(event.get("task_id") or "") == commit.task_id
                    and str(event.get("arm") or "") == commit.arm
                    and str(event.get("state") or "")
                    == commit.terminal_state
                    and str(event.get("state_hash") or "")
                    == commit.terminal_state_hash
                )
            ]
            if len(matching_events) != 1:
                raise EvidenceCommitIntegrityError(
                    "terminal grade authority does not resolve to exactly "
                    "one immutable terminal arm event for execution "
                    f"{history.execution_id}: {commit.grade_id}"
                )
            resolved_events[commit.terminal_state_hash] = matching_events[0]

        if len(resolved_events) != 1:
            raise EvidenceCommitIntegrityError(
                "published terminal grade authority must resolve to exactly "
                "one terminal arm event for execution "
                f"{history.execution_id}"
            )

        source_terminal_commits: list[GradeTerminalCommit] = []
        for event in resolved_events.values():
            payload = event.get("payload")
            outcome = (
                payload.get("outcome")
                if isinstance(payload, Mapping)
                else None
            )
            raw_reference = (
                outcome.get("grade_revision")
                if isinstance(outcome, Mapping)
                else None
            )
            if not isinstance(raw_reference, Mapping):
                raise EvidenceCommitIntegrityError(
                    "terminal arm event grade reference is missing for "
                    f"execution {history.execution_id}"
                )
            try:
                reference = GradeRevisionRef.from_mapping(raw_reference)
            except (TypeError, ValueError) as exc:
                raise EvidenceCommitIntegrityError(
                    "terminal arm event grade reference is invalid for "
                    f"execution {history.execution_id}"
                ) from exc
            try:
                authority_revision = authority_gradebook.get_revision(
                    reference.grade_id
                )
                authority_commit = (
                    authority_gradebook.get_terminal_commit(
                        reference.grade_id
                    )
                )
            except (
                GradeIntegrityError,
                GradeNotFoundError,
                GradeValidationError,
            ) as exc:
                raise EvidenceCommitIntegrityError(
                    "terminal arm event grade reference is unavailable from "
                    "source authority for execution "
                    f"{history.execution_id}"
                ) from exc
            if (
                reference
                != GradeRevisionRef.from_revision(authority_revision)
                or authority_revision.run_envelope.frozen_result_hash
                != history.run.frozen_result_hash
            ):
                raise EvidenceCommitIntegrityError(
                    "terminal arm event grade reference does not match source "
                    "authority for execution "
                    f"{history.execution_id}"
                )
            if (
                authority_commit is None
                or authority_commit.grade_revision_hash
                != reference.revision_hash
                or authority_commit.experiment_id
                != str(event.get("experiment_id") or "")
                or authority_commit.task_id
                != str(event.get("task_id") or "")
                or authority_commit.arm != str(event.get("arm") or "")
                or authority_commit.terminal_state
                != str(event.get("state") or "")
                or authority_commit.terminal_state_hash
                != str(event.get("state_hash") or "")
            ):
                raise EvidenceCommitIntegrityError(
                    "terminal arm event grade reference lacks matching grade "
                    "source authority for execution "
                    f"{history.execution_id}: {reference.grade_id}"
                )
            source_terminal_commits.append(authority_commit)
        return tuple(source_terminal_commits)

    def _persist_planning_projection(
        self,
        request: EvidenceCommitRequest,
        *,
        prior_phase: str,
    ) -> TraceLifecycleRevision:
        """Persist the planning projection first within the commit workflow.

        The input graph and grades are already frozen. This ordering proves
        structural stage partitioning, not pre-execution wall-clock chronology.
        """
        planning = request.trace_graph.lifecycle_revision(
            TraceLifecycleStage.PLANNING
        )
        if not planning.nodes:
            raise EvidenceCommitIntegrityError(
                "trace planning projection must contain immutable planning "
                "nodes"
            )
        try:
            with TraceGraphStore(self.trace_store_path) as store:
                lifecycle = store.list_lifecycle_revisions()
                if (
                    not lifecycle
                    and _PHASE_INDEX.get(prior_phase, -1)
                    >= _PHASE_INDEX["grades_verified"]
                ):
                    raise TraceGraphError(
                        "refusing to reconstruct a missing planning "
                        "projection after evidence-commit grade verification"
                    )
                return store.append_lifecycle_revision(
                    TraceLifecycleStage.PLANNING,
                    planning,
                )
        except (OSError, sqlite3.DatabaseError, TraceGraphError) as exc:
            raise EvidenceCommitIntegrityError(
                "trace planning projection failed closed: "
                f"{exc}"
            ) from exc

    def _persist_trace(
        self,
        request: EvidenceCommitRequest,
        histories: tuple[EvidenceGradeHistory, ...],
    ) -> tuple[
        TraceGraph,
        ClosureResult,
        tuple[TraceNode, ...],
        Mapping[str, Any],
    ]:
        try:
            with TraceGraphStore(self.trace_store_path) as store:
                for stage in tuple(TraceLifecycleStage)[1:]:
                    store.append_lifecycle_revision(
                        stage,
                        request.trace_graph.lifecycle_revision(stage),
                    )
                graph = store.load_lifecycle_revision(
                    TraceLifecycleStage.DECISION
                )
                lifecycle = store.list_lifecycle_revisions()
                live_graph = store.load()
        except (OSError, sqlite3.DatabaseError, TraceGraphError) as exc:
            raise EvidenceCommitIntegrityError(
                "trace lifecycle persistence failed closed: "
                f"{exc}"
            ) from exc
        if graph.canonical_bytes() != request.trace_graph.canonical_bytes():
            raise EvidenceCommitIntegrityError(
                "persisted TraceGraphStore bytes differ from requested graph"
            )
        if live_graph.canonical_bytes() != graph.canonical_bytes():
            raise EvidenceCommitIntegrityError(
                "persisted TraceGraphStore contains unversioned trace records"
            )
        with GradeBook(self.gradebook_path) as gradebook:
            closure = graph.validate_closure(
                now=request.closure_time,
                expected_binding=request.trace_graph.expected_binding,
                decision_grade_validator=gradebook,
            )
        if not closure.ok:
            raise EvidenceCommitIntegrityError(
                "persisted trace graph does not close: "
                + canonical_json_bytes(closure.to_dict()).decode("utf-8")
            )
        self._validate_trace_grade_links(graph, histories)
        self._validate_trace_grade_decisions(
            graph,
            request.promotion,
        )
        promotion_trace = graph.promotion_trace(request.promotion)
        return (
            graph,
            closure,
            promotion_trace,
            _trace_lifecycle_identity(lifecycle),
        )

    def _validate_trace_grade_links(
        self,
        graph: TraceGraph,
        histories: tuple[EvidenceGradeHistory, ...],
    ) -> None:
        """Require every immutable grade revision and invalidation in trace."""
        observed_edges = {
            (edge.source, edge.relation, edge.target)
            for edge in graph.edges
        }
        for history in histories:
            run_nodes = [
                node
                for node in graph.nodes
                if node.identity.node_type is NodeType.RUN
                and node.identity.revision_hash
                == history.run.run_envelope_hash
                and node.attributes.get("runtime_run_id")
                == history.run.run_id
            ]
            artifact_nodes = [
                node
                for node in graph.nodes
                if node.identity.node_type is NodeType.ART
                and node.identity.revision_hash
                == history.run.frozen_result_hash
                and node.attributes.get("frozen_result_hash")
                == history.run.frozen_result_hash
            ]
            if len(run_nodes) != 1 or len(artifact_nodes) != 1:
                raise EvidenceCommitIntegrityError(
                    "trace graph does not uniquely bind the GradeBook run "
                    f"for execution {history.execution_id}"
                )
            run_node = run_nodes[0]
            artifact_node = artifact_nodes[0]
            if (
                artifact_node.identity,
                EdgeType.DERIVED_FROM,
                run_node.identity,
            ) not in observed_edges:
                raise EvidenceCommitIntegrityError(
                    "trace graph lacks result-to-run provenance "
                    f"for execution {history.execution_id}"
                )

            revision_nodes: dict[str, TraceNode] = {}
            for revision in history.revisions:
                expected_attributes = {
                    "record_kind": "grade_revision",
                    **revision.to_dict(),
                }
                matching = [
                    node
                    for node in graph.nodes
                    if node.identity.node_type is NodeType.GRADE
                    and node.identity.revision_hash
                    == revision.revision_hash
                    and node.attributes.get("grade_id")
                    == revision.grade_id
                    and canonical_json_bytes(
                        node.to_dict()["attributes"]
                    )
                    == canonical_json_bytes(expected_attributes)
                ]
                if len(matching) != 1:
                    raise EvidenceCommitIntegrityError(
                        "trace graph does not preserve the complete GradeBook "
                        "lineage for execution "
                        f"{history.execution_id}: missing revision "
                        f"{revision.grade_id}"
                    )
                revision_node = matching[0]
                if (
                    revision_node.verifier_id != revision.verifier_id
                    or revision_node.verifier_revision_hash
                    != revision.verifier_implementation_hash
                ):
                    raise EvidenceCommitIntegrityError(
                        "trace graph grade verifier provenance differs from "
                        f"GradeBook revision {revision.grade_id}"
                    )
                if (
                    revision_node.identity,
                    EdgeType.EVALUATES,
                    artifact_node.identity,
                ) not in observed_edges:
                    raise EvidenceCommitIntegrityError(
                        "trace graph lacks GradeBook-to-result provenance "
                        f"for revision {revision.grade_id}"
                    )
                revision_nodes[revision.grade_id] = revision_node

            for revision in history.revisions:
                if revision.supersedes_grade_id is None:
                    continue
                superseded = revision_nodes.get(
                    revision.supersedes_grade_id
                )
                if superseded is None or (
                    revision_nodes[revision.grade_id].identity,
                    EdgeType.SUPERSEDES,
                    superseded.identity,
                ) not in observed_edges:
                    raise EvidenceCommitIntegrityError(
                        "trace graph does not preserve the complete GradeBook "
                        "lineage: missing supersession edge for "
                        f"{revision.grade_id}"
                    )

            for invalidation in history.invalidations:
                target = revision_nodes.get(invalidation.grade_id)
                if target is None:
                    raise EvidenceCommitIntegrityError(
                        "trace graph invalidation references an absent grade "
                        f"revision: {invalidation.grade_id}"
                    )
                expected_attributes = {
                    "record_kind": "grade_invalidation",
                    **invalidation.to_dict(),
                }
                matching = [
                    node
                    for node in graph.nodes
                    if node.identity.node_type is NodeType.GRADE
                    and node.identity.revision_hash
                    == invalidation.invalidation_hash
                    and canonical_json_bytes(
                        node.to_dict()["attributes"]
                    )
                    == canonical_json_bytes(expected_attributes)
                ]
                if len(matching) != 1:
                    raise EvidenceCommitIntegrityError(
                        "trace graph does not preserve the complete GradeBook "
                        "lineage for execution "
                        f"{history.execution_id}: missing invalidation "
                        f"{invalidation.invalidation_id}"
                    )
                invalidation_node = matching[0]
                if (
                    invalidation_node.verifier_id != target.verifier_id
                    or invalidation_node.verifier_revision_hash
                    != target.verifier_revision_hash
                    or (
                        invalidation_node.identity,
                        EdgeType.INVALIDATES,
                        target.identity,
                    )
                    not in observed_edges
                ):
                    raise EvidenceCommitIntegrityError(
                        "trace graph does not preserve the complete GradeBook "
                        "invalidation lineage for "
                        f"{invalidation.invalidation_id}"
                    )

    def _validate_trace_grade_decisions(
        self,
        graph: TraceGraph,
        promotion: TraceIdentity,
        grade_authority: GradeBook | None = None,
    ) -> None:
        """Bind promoted decisions to exact, resolved GradeBook citations."""
        decision_identities = {
            edge.target
            for edge in graph.edges
            if edge.source == promotion
            and edge.relation is EdgeType.PROMOTES
            and edge.target.node_type is NodeType.DEC
        }
        if not decision_identities:
            raise EvidenceCommitIntegrityError(
                "promotion has no grade-citing decision"
            )
        nodes_by_identity = {
            node.identity: node for node in graph.nodes
        }
        authority_context = (
            nullcontext(grade_authority)
            if grade_authority is not None
            else GradeBook(self.gradebook_path)
        )
        with authority_context as gradebook:
            for decision_identity in sorted(
                decision_identities,
                key=lambda identity: identity.canonical_key,
            ):
                decision = nodes_by_identity[decision_identity]
                raw_citations = decision.attributes.get(
                    "grade_citations"
                )
                if (
                    not isinstance(raw_citations, (list, tuple))
                    or not raw_citations
                ):
                    raise EvidenceCommitIntegrityError(
                        "promoted decision lacks immutable grade citations: "
                        f"{decision.identity.canonical_key}"
                    )
                try:
                    citations = tuple(
                        DecisionGradeCitation.from_mapping(item)
                        for item in raw_citations
                        if isinstance(item, Mapping)
                    )
                except GradeValidationError as exc:
                    raise EvidenceCommitIntegrityError(
                        "promoted decision has an invalid grade citation: "
                        f"{exc}"
                    ) from exc
                if len(citations) != len(raw_citations):
                    raise EvidenceCommitIntegrityError(
                        "promoted decision grade citations must be objects"
                    )
                validation = gradebook.validate_decision(citations)
                if not validation.accepted:
                    raise EvidenceCommitIntegrityError(
                        "promoted decision grade validation failed: "
                        + canonical_json_bytes(
                            validation.to_dict()
                        ).decode("utf-8")
                    )

                analysis_identities = {
                    edge.target
                    for edge in graph.edges
                    if edge.source == decision.identity
                    and edge.relation is EdgeType.DERIVED_FROM
                    and edge.target.node_type is NodeType.ANL
                }
                traced_grade_refs = {
                    (
                        str(nodes_by_identity[edge.target].attributes.get(
                            "grade_id"
                        ) or ""),
                        edge.target.revision_hash,
                    )
                    for edge in graph.edges
                    if edge.source in analysis_identities
                    and edge.relation is EdgeType.DERIVED_FROM
                    and edge.target.node_type is NodeType.GRADE
                    and nodes_by_identity[
                        edge.target
                    ].attributes.get("record_kind") == "grade_revision"
                }
                effective_citation_refs = {
                    (
                        citation.resolution_grade_id
                        or citation.grade_id,
                        citation.resolution_revision_hash
                        or citation.revision_hash,
                    )
                    for citation in citations
                }
                if (
                    not traced_grade_refs
                    or traced_grade_refs != effective_citation_refs
                ):
                    raise EvidenceCommitIntegrityError(
                        "promoted decision grade citations do not match the "
                        "grades on its traced analysis path"
                    )

    def _load_or_stage_artifacts(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        histories: tuple[EvidenceGradeHistory, ...],
        graph: TraceGraph,
        closure: ClosureResult,
        precommit_heads: Mapping[str, Any],
        trace_lifecycle_identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        row = self._load_commit_row(request.commit_id)
        stored = row["materialization_json"]
        if stored:
            materialization = _decode_json_object(
                stored,
                field="materialization_json",
            )
            self._verify_materialization(
                request=request,
                request_hash=request_hash,
                materialization=materialization,
                graph=graph,
                closure=closure,
                trace_lifecycle_identity=trace_lifecycle_identity,
            )
            return materialization

        precommit_streams = self._verify_precommit_streams(
            request=request,
            precommit_heads=precommit_heads,
        )
        aggregate_events = precommit_streams[request.aggregate_run_id]
        aggregate_head = precommit_heads[request.aggregate_run_id]
        projection = rebuild_projection(
            aggregate_events,
            initial=initial_tracer_evidence_projection(
                request.aggregate_run_id
            ),
            reducer=reduce_tracer_evidence_projection,
            expected_head_hash=str(aggregate_head["head_event_hash"]),
            expected_run_id=request.aggregate_run_id,
        )
        self._validate_projection(projection, request)
        projection_bytes = canonical_json_bytes(projection)

        artifacts = list(request.artifacts)
        artifacts.extend(
            self._generated_artifacts(
                request=request,
                histories=histories,
                graph=graph,
                closure=closure,
                projection_bytes=projection_bytes,
                precommit_heads=precommit_heads,
            )
        )
        roles = [artifact.role for artifact in artifacts]
        paths = [artifact.relative_path for artifact in artifacts]
        if len(set(roles)) != len(roles) or len(set(paths)) != len(paths):
            raise EvidenceCommitConflict(
                "generated evidence artifacts collide with caller artifacts"
            )

        descriptors: list[dict[str, Any]] = []
        for artifact in sorted(
            artifacts,
            key=lambda item: (item.role, item.relative_path),
        ):
            descriptor = self.artifact_store.put_bytes(
                artifact.content,
                name=artifact.relative_path,
                media_type=artifact.media_type,
            )
            descriptors.append(
                {
                    **descriptor,
                    "role": artifact.role,
                    "ref": artifact.relative_path,
                }
            )

        manifest_descriptors = [
            {
                key: descriptor[key]
                for key in ("name", "digest", "size", "media_type", "uri")
            }
            for descriptor in descriptors
        ]
        manifest = self.artifact_store.create_manifest(
            manifest_descriptors,
            metadata={
                "schema_version": EVIDENCE_COMMIT_SCHEMA_VERSION,
                "commit_id": request.commit_id,
                "request_hash": request_hash,
                "aggregate_run_id": request.aggregate_run_id,
                "mode": request.mode,
                "claim_cap": request.claim_cap,
                "operational_efficacy_evidence": False,
                "state_snapshot_cut": "before_artifact_manifest_event",
                "precommit_heads": dict(precommit_heads),
                "trace_lifecycle": dict(trace_lifecycle_identity),
                "artifact_roles": [
                    {
                        "role": descriptor["role"],
                        "name": descriptor["name"],
                    }
                    for descriptor in descriptors
                ],
            },
        )
        self.artifact_store.verify_manifest(manifest)
        manifest_attestation = (
            self.artifact_store.create_manifest_attestation(
                manifest,
                signer=self.signer,
                verifier=self.verifier,
                created_at=request.checkpoint_created_at,
                manifest_name="artifacts/artifact-manifest.json",
            )
        )
        manifest_attestation_identity = _manifest_attestation_identity(
            manifest_attestation
        )
        projection_descriptor = next(
            descriptor
            for descriptor in descriptors
            if descriptor["role"] == "tracer_projection"
        )
        materialization = {
            "schema_version": EVIDENCE_COMMIT_SCHEMA_VERSION,
            "artifacts": descriptors,
            "artifact_manifest": manifest,
            "artifact_manifest_attestation": manifest_attestation,
            "artifact_manifest_attestation_identity": (
                manifest_attestation_identity
            ),
            "trace_lifecycle": dict(trace_lifecycle_identity),
            "projection_ref": projection_descriptor["ref"],
            "projection_sha256": projection_descriptor["digest"]["sha256"],
        }
        self._store_materialization(
            request.commit_id,
            materialization,
        )
        for artifact in sorted(
            artifacts,
            key=lambda item: (item.role, item.relative_path),
        ):
            _write_evidence_file(
                self.root,
                artifact.relative_path,
                artifact.content,
            )
        _write_evidence_file(
            self.root,
            "artifacts/artifact-manifest.json",
            canonical_json_bytes(manifest),
        )
        _write_evidence_file(
            self.root,
            "artifacts/artifact-manifest.attestation.json",
            canonical_json_bytes(manifest_attestation),
        )
        self._observe_phase("artifacts_staged")
        return materialization

    def _generated_artifacts(
        self,
        *,
        request: EvidenceCommitRequest,
        histories: tuple[EvidenceGradeHistory, ...],
        graph: TraceGraph,
        closure: ClosureResult,
        projection_bytes: bytes,
        precommit_heads: Mapping[str, Any],
    ) -> tuple[EvidenceArtifact, ...]:
        run_references = {
            "schema_version": "harness-canonical-run-references/v1",
            "aggregate_run_id": request.aggregate_run_id,
            "registered_run_ids": list(request.registered_run_ids),
            "state_registrations": [
                self._state_registration_reference(run_id)
                for run_id in request.registered_run_ids
            ],
            "runs": [
                {
                    "execution_id": history.execution_id,
                    **history.run.to_dict(),
                }
                for history in histories
            ],
        }
        result_references = {
            "schema_version": "harness-canonical-result-references/v1",
            "results": [
                {
                    "execution_id": history.execution_id,
                    "run_id": history.run.run_id,
                    "run_envelope_hash": history.run.run_envelope_hash,
                    "frozen_result_hash": history.run.frozen_result_hash,
                    "current_grade_id": history.revisions[-1].grade_id,
                    "current_grade_revision_hash": (
                        history.revisions[-1].revision_hash
                    ),
                }
                for history in histories
            ],
        }
        grade_export = {
            "schema_version": "harness-grade-history-export/v1",
            "histories": [history.to_dict() for history in histories],
        }
        trace_payload = canonical_json_bytes(
            {
                "graph": graph.to_dict(),
                "closure": closure.to_dict(),
            }
        )
        supplied_trace = next(
            artifact
            for artifact in request.artifacts
            if artifact.role == "trace_graph"
        )
        if supplied_trace.content != trace_payload:
            raise EvidenceCommitIntegrityError(
                "trace_graph artifact differs from persisted graph and closure"
            )

        raw_state_path = str(getattr(self.state, "db_path", ""))
        if (
            not raw_state_path
            or raw_state_path == ":memory:"
            or is_postgres_state_dsn(raw_state_path)
        ):
            raise EvidenceCommitIntegrityError(
                "stable state export currently requires the SQLite State adapter"
            )
        state_path = _absolute_no_follow_path(raw_state_path)
        snapshots = (
            (
                "state_snapshot",
                "snapshots/state.pre-manifest.json",
                _export_sqlite_database(
                    state_path,
                    logical_name="state",
                    snapshot_metadata={
                        "cut": "before_artifact_manifest_event",
                        "precommit_heads": dict(precommit_heads),
                    },
                ),
            ),
            (
                "experiment_snapshot",
                "snapshots/experiment-store.json",
                _export_sqlite_database(
                    self.experiment_db_path,
                    logical_name="experiment",
                ),
            ),
            (
                "gradebook_snapshot",
                "snapshots/gradebook.json",
                _export_sqlite_database(
                    self.gradebook_path,
                    logical_name="gradebook",
                ),
            ),
            (
                "trace_store_snapshot",
                "snapshots/trace-store.json",
                _export_sqlite_database(
                    self.trace_store_path,
                    logical_name="trace_store",
                ),
            ),
        )
        generated = [
            EvidenceArtifact(
                role="canonical_run_references",
                relative_path="artifacts/canonical-run-references.json",
                content=canonical_json_bytes(run_references),
            ),
            EvidenceArtifact(
                role="canonical_result_references",
                relative_path="artifacts/canonical-result-references.json",
                content=canonical_json_bytes(result_references),
            ),
            EvidenceArtifact(
                role="grade_revisions",
                relative_path="artifacts/grade-revisions.json",
                content=canonical_json_bytes(grade_export),
            ),
            EvidenceArtifact(
                role="tracer_projection",
                relative_path="artifacts/tracer-projection.json",
                content=projection_bytes,
            ),
        ]
        generated.extend(
            EvidenceArtifact(
                role=role,
                relative_path=relative_path,
                content=content,
            )
            for role, relative_path, content in snapshots
        )
        return tuple(generated)

    def _state_registration_reference(
        self,
        run_id: str,
    ) -> dict[str, Any]:
        registration = self._validated_state_registration(run_id)
        run_payload = registration["run"]
        snapshot_payload = registration["snapshot"]
        return {
            "run_id": run_id,
            "session_id": run_payload.get("session_id"),
            "rollout_path": run_payload.get("rollout_path"),
            "status": run_payload.get("status"),
            "target_kind": snapshot_payload.get("target_kind"),
            "run_record_sha256": sha256_hex(
                canonical_json_bytes(run_payload)
            ),
            "snapshot_record_sha256": sha256_hex(
                canonical_json_bytes(snapshot_payload)
            ),
        }

    def _validated_state_registration(
        self,
        run_id: str,
    ) -> dict[str, Any]:
        try:
            return validate_run_registration_authority(
                state=self.state,
                run_id=run_id,
            )
        except RuntimeError as exc:
            raise EvidenceCommitIntegrityError(
                f"canonical State registration is invalid for {run_id}: "
                f"{exc}"
            ) from exc

    def _verify_materialization(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        materialization: Mapping[str, Any],
        graph: TraceGraph,
        closure: ClosureResult,
        trace_lifecycle_identity: Mapping[str, Any] | None,
    ) -> None:
        if (
            materialization.get("schema_version")
            != EVIDENCE_COMMIT_SCHEMA_VERSION
        ):
            raise EvidenceCommitIntegrityError(
                "persisted materialization schema version is unsupported"
            )
        manifest = materialization.get("artifact_manifest")
        if not isinstance(manifest, Mapping):
            raise EvidenceCommitIntegrityError(
                "persisted materialization lacks an artifact manifest"
            )
        metadata = manifest.get("metadata")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("schema_version")
            != EVIDENCE_COMMIT_SCHEMA_VERSION
            or metadata.get("commit_id") != request.commit_id
            or metadata.get("request_hash") != request_hash
        ):
            raise EvidenceCommitIntegrityError(
                "persisted artifact manifest schema version or identity "
                "mismatch"
            )
        self.artifact_store.verify_manifest(manifest)
        manifest_has_trace_lifecycle = "trace_lifecycle" in metadata
        manifest_trace_lifecycle = metadata.get("trace_lifecycle")
        has_trace_lifecycle = "trace_lifecycle" in materialization
        materialized_trace_lifecycle = materialization.get(
            "trace_lifecycle"
        )
        if trace_lifecycle_identity is None:
            if has_trace_lifecycle or manifest_has_trace_lifecycle:
                raise EvidenceCommitIntegrityError(
                    "persisted trace lifecycle receipts are missing"
                )
        elif (
            not has_trace_lifecycle
            or not manifest_has_trace_lifecycle
            or not isinstance(materialized_trace_lifecycle, Mapping)
            or not isinstance(manifest_trace_lifecycle, Mapping)
            or canonical_json_bytes(materialized_trace_lifecycle)
            != canonical_json_bytes(trace_lifecycle_identity)
            or canonical_json_bytes(manifest_trace_lifecycle)
            != canonical_json_bytes(trace_lifecycle_identity)
        ):
            raise EvidenceCommitIntegrityError(
                "persisted trace lifecycle identity mismatch"
            )
        has_attestation = "artifact_manifest_attestation" in materialization
        has_attestation_identity = (
            "artifact_manifest_attestation_identity" in materialization
        )
        attestation = materialization.get("artifact_manifest_attestation")
        recorded_attestation_identity = materialization.get(
            "artifact_manifest_attestation_identity"
        )
        if has_attestation != has_attestation_identity:
            raise EvidenceCommitIntegrityError(
                "persisted materialization has incomplete manifest "
                "attestation evidence"
            )
        if has_attestation:
            if (
                not isinstance(attestation, Mapping)
                or not isinstance(recorded_attestation_identity, Mapping)
            ):
                raise EvidenceCommitIntegrityError(
                    "persisted materialization has invalid manifest "
                    "attestation evidence"
                )
            try:
                canonical_manifest_bytes = canonical_json_bytes(manifest)
                verify_artifact_manifest_attestation(
                    attestation,
                    manifest=manifest,
                    manifest_bytes=self.artifact_store.read_bytes(
                        sha256_hex(canonical_manifest_bytes)
                    ),
                    verifier=self.verifier,
                )
            except (ArtifactIntegrityError, TypeError, ValueError) as exc:
                raise EvidenceCommitIntegrityError(
                    "persisted artifact manifest attestation is invalid"
                ) from exc
            attestation_identity = _manifest_attestation_identity(
                attestation
            )
            if (
                canonical_json_bytes(recorded_attestation_identity)
                != canonical_json_bytes(attestation_identity)
                or attestation_identity["manifest_hash"]
                != manifest["manifest_hash"]
                or attestation_identity["created_at"]
                != request.checkpoint_created_at
            ):
                raise EvidenceCommitIntegrityError(
                    "persisted artifact manifest attestation identity mismatch"
                )
        else:
            raise EvidenceCommitIntegrityError(
                "persisted materialization lacks a manifest attestation"
            )
        descriptors = materialization.get("artifacts")
        if not isinstance(descriptors, list):
            raise EvidenceCommitIntegrityError(
                "persisted materialization lacks artifact descriptors"
            )
        manifest_descriptors = manifest.get("artifacts")
        if not isinstance(manifest_descriptors, list):
            raise EvidenceCommitIntegrityError(
                "persisted artifact manifest lacks descriptors"
            )
        canonical_manifest_descriptors = {
            canonical_json_bytes(descriptor)
            for descriptor in manifest_descriptors
        }
        supplied = {
            artifact.role: artifact for artifact in request.artifacts
        }
        observed_roles: set[str] = set()
        for descriptor in descriptors:
            if not isinstance(descriptor, Mapping):
                raise EvidenceCommitIntegrityError(
                    "artifact descriptor is not an object"
                )
            role = str(descriptor.get("role") or "")
            reference = str(descriptor.get("ref") or "")
            digest = descriptor.get("digest")
            if (
                not role
                or role in observed_roles
                or not isinstance(digest, Mapping)
            ):
                raise EvidenceCommitIntegrityError(
                    "artifact descriptor role or digest is invalid"
                )
            observed_roles.add(role)
            canonical_descriptor = {
                key: descriptor[key]
                for key in ("name", "digest", "size", "media_type", "uri")
            }
            if (
                canonical_json_bytes(canonical_descriptor)
                not in canonical_manifest_descriptors
            ):
                raise EvidenceCommitIntegrityError(
                    f"materialized artifact is absent from manifest: {role}"
                )
            content = self.artifact_store.read_bytes(
                str(digest.get("sha256") or "")
            )
            try:
                _write_evidence_file(self.root, reference, content)
            except EvidenceCommitConflict as exc:
                raise EvidenceCommitIntegrityError(
                    f"materialized artifact differs from CAS: {reference}"
                ) from exc
            expected = supplied.get(role)
            if expected is not None and (
                expected.relative_path != reference
                or expected.content != content
            ):
                raise EvidenceCommitIntegrityError(
                    f"persisted artifact differs from request: {role}"
                )
        if not _REQUIRED_ARTIFACT_ROLES <= observed_roles:
            raise EvidenceCommitIntegrityError(
                "persisted materialization lost required artifacts"
            )
        trace_descriptor = next(
            descriptor
            for descriptor in descriptors
            if descriptor["role"] == "trace_graph"
        )
        trace_content = self.artifact_store.read_bytes(
            str(trace_descriptor["digest"]["sha256"])
        )
        if trace_content != canonical_json_bytes(
            {
                "graph": graph.to_dict(),
                "closure": closure.to_dict(),
            }
        ):
            raise EvidenceCommitIntegrityError(
                "persisted trace artifact differs from TraceGraphStore"
            )
        try:
            _write_evidence_file(
                self.root,
                "artifacts/artifact-manifest.json",
                canonical_json_bytes(manifest),
            )
        except EvidenceCommitConflict as exc:
            raise EvidenceCommitIntegrityError(
                "materialized artifact manifest differs from outbox"
            ) from exc
        if isinstance(attestation, Mapping):
            try:
                _write_evidence_file(
                    self.root,
                    "artifacts/artifact-manifest.attestation.json",
                    canonical_json_bytes(attestation),
                )
            except EvidenceCommitConflict as exc:
                raise EvidenceCommitIntegrityError(
                    "materialized artifact manifest attestation differs from "
                    "outbox"
                ) from exc

    def _manifest_event_payload(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        materialization: Mapping[str, Any],
    ) -> dict[str, Any]:
        manifest = materialization["artifact_manifest"]
        return {
            "schema_version": EVIDENCE_COMMIT_SCHEMA_VERSION,
            "commit_id": request.commit_id,
            "request_hash": request_hash,
            "mode": request.mode,
            "claim_cap": request.claim_cap,
            "operational_efficacy_evidence": False,
            "artifact_manifest": manifest,
            "artifact_manifest_hash": manifest["manifest_hash"],
            "artifact_manifest_attestation": materialization[
                "artifact_manifest_attestation_identity"
            ],
            "trace_lifecycle": materialization["trace_lifecycle"],
            "projection": {
                "schema_version": TRACER_PROJECTION_SCHEMA_VERSION,
                "ref": materialization["projection_ref"],
                "sha256": materialization["projection_sha256"],
            },
            "state_snapshot_cut": "before_artifact_manifest_event",
        }

    def _append_or_verify_manifest_event(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        materialization: Mapping[str, Any],
        precommit_heads: Mapping[str, Any],
        allow_append_suffix: bool = False,
    ) -> dict[str, Any]:
        manifest = materialization["artifact_manifest"]
        payload = self._manifest_event_payload(
            request=request,
            request_hash=request_hash,
            materialization=materialization,
        )
        detail: dict[str, Any]
        existing: dict[str, Any]
        with self._lock, self._outbox_connection() as conn:
            # BEGIN IMMEDIATE is the cross-instance publication mutex. The
            # immutable ledger event is committed before this outbox
            # transaction records its identity. If the process dies between
            # those commits, the next holder discovers the event by commit_id
            # and reconciles it instead of appending a second event.
            _begin_outbox_transaction(conn)
            observed = self._find_manifest_event(request)
            if observed is None:
                if allow_append_suffix:
                    self._verify_append_only_event_suffixes(
                        request=request,
                        precommit_heads=precommit_heads,
                    )
                else:
                    self._assert_precommit_streams(
                        request=request,
                        precommit_heads=precommit_heads,
                    )
                event_id = self.state.write_evidence_commit_event(
                    run_id=request.aggregate_run_id,
                    payload=payload,
                    capability=self._event_write_capability,
                    ts=request.manifest_event_ts,
                )
                observed = next(
                    (
                        event
                        for event in self._read_all_events(
                            request.aggregate_run_id
                        )
                        if int(event["event_id"]) == int(event_id)
                    ),
                    None,
                )
                if observed is None:
                    raise EvidenceCommitIntegrityError(
                        "artifact-manifest event was not durably readable"
                    )
            existing = observed
            self._verify_manifest_event(
                request=request,
                request_hash=request_hash,
                event=existing,
                materialization=materialization,
                precommit_heads=precommit_heads,
                require_frozen_heads=not allow_append_suffix,
            )
            detail = {
                "event_id": existing["event_id"],
                "event_hash": existing["event_hash"],
                "artifact_manifest_hash": manifest["manifest_hash"],
                "artifact_manifest_attestation": materialization[
                    "artifact_manifest_attestation_identity"
                ],
                "trace_lifecycle": materialization["trace_lifecycle"],
            }
            self._advance_in_transaction(
                conn,
                commit_id=request.commit_id,
                phase="manifest_appended",
                detail=detail,
                now=_now_ms(),
            )
            conn.commit()
        self._observe_phase("manifest_appended")
        return existing

    def _verify_manifest_event(
        self,
        *,
        request: EvidenceCommitRequest,
        request_hash: str,
        event: Mapping[str, Any],
        materialization: Mapping[str, Any],
        precommit_heads: Mapping[str, Any],
        require_frozen_heads: bool = True,
    ) -> None:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise EvidenceCommitIntegrityError(
                "artifact-manifest event payload is invalid"
            )
        try:
            self.state.assert_evidence_commit_event_authority(
                run_id=request.aggregate_run_id,
                commit_id=request.commit_id,
                event_id=int(event["event_id"]),
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise EvidenceCommitIntegrityError(
                "artifact-manifest event authority claim is invalid"
            ) from exc
        expected_manifest = materialization["artifact_manifest"]
        expected_payload = self._manifest_event_payload(
            request=request,
            request_hash=request_hash,
            materialization=materialization,
        )
        if (
            event.get("kind") != EVIDENCE_COMMIT_EVENT_KIND
            or event.get("source") != EVIDENCE_COMMIT_EVENT_SOURCE
            or canonical_json_bytes(payload)
            != canonical_json_bytes(expected_payload)
            or event.get("artifact_manifest_hash")
            != expected_manifest["manifest_hash"]
        ):
            raise EvidenceCommitConflict(
                "existing artifact-manifest event conflicts with commit input"
            )
        if not require_frozen_heads:
            return
        events = self._read_all_events(request.aggregate_run_id)
        precommit = precommit_heads[request.aggregate_run_id]
        if (
            len(events) != int(precommit["event_count"]) + 1
            or events[-1]["event_id"] != event["event_id"]
            or events[-1]["event_hash"] != event["event_hash"]
        ):
            raise EvidenceCommitIntegrityError(
                "artifact-manifest event is not the exact aggregate stream head"
            )
        for run_id in request.registered_run_ids:
            if run_id == request.aggregate_run_id:
                continue
            self._assert_head_matches(
                run_id,
                precommit_heads[run_id],
            )

    def _verify_frozen_checkpoint_streams(
        self,
        *,
        request: EvidenceCommitRequest,
        precommit_heads: Mapping[str, Any],
        manifest_event: Mapping[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        """Validate every frozen stream before any signer can be invoked."""
        expected_heads = {
            run_id: dict(precommit_heads[run_id])
            for run_id in request.registered_run_ids
        }
        aggregate_precommit = expected_heads[request.aggregate_run_id]
        expected_heads[request.aggregate_run_id] = {
            "event_count": int(aggregate_precommit["event_count"]) + 1,
            "head_event_id": int(manifest_event["event_id"]),
            "head_event_hash": str(manifest_event["event_hash"]),
        }

        streams: dict[str, list[dict[str, Any]]] = {}
        for run_id in request.registered_run_ids:
            events = self._read_all_events(run_id)
            expected = expected_heads[run_id]
            if not events:
                raise EvidenceCommitIntegrityError(
                    "frozen evidence stream verification failed for "
                    f"{run_id}: stream is empty"
                )
            head = events[-1]
            if (
                len(events) != int(expected["event_count"])
                or int(head["event_id"]) != int(expected["head_event_id"])
                or str(head["event_hash"])
                != str(expected["head_event_hash"])
            ):
                raise EvidenceCommitIntegrityError(
                    "frozen evidence stream verification failed for "
                    f"{run_id}: observed head differs from the expected cut"
                )
            verification = verify_event_chain(
                events,
                expected_head_hash=str(expected["head_event_hash"]),
                expected_run_id=run_id,
            )
            if (
                not verification.valid
                or not verification.truncation_checked
                or verification.event_count != int(expected["event_count"])
                or verification.head_event_id != expected["head_event_id"]
            ):
                raise EvidenceCommitIntegrityError(
                    "frozen evidence stream verification failed for "
                    f"{run_id}: {verification.to_dict()}"
                )
            streams[run_id] = events
        return streams

    def _verify_recovery_checkpoint_streams(
        self,
        *,
        request: EvidenceCommitRequest,
        precommit_heads: Mapping[str, Any],
        manifest_event: Mapping[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        """Recover the immutable event cuts while ignoring later valid tails."""
        streams: dict[str, list[dict[str, Any]]] = {}
        manifest_event_id = int(manifest_event["event_id"])
        manifest_event_hash = str(manifest_event["event_hash"])
        for run_id in request.registered_run_ids:
            events = self._read_all_events(run_id)
            prefix = self._verify_event_prefix(
                run_id=run_id,
                events=events,
                expected=precommit_heads[run_id],
            )
            if run_id != request.aggregate_run_id:
                streams[run_id] = prefix
                continue
            manifest_index = next(
                (
                    index
                    for index, event in enumerate(events)
                    if int(event["event_id"]) == manifest_event_id
                ),
                None,
            )
            if manifest_index is None or manifest_index < len(prefix):
                raise EvidenceCommitIntegrityError(
                    "artifact-manifest event does not extend the immutable "
                    "aggregate prefix"
                )
            committed = events[: manifest_index + 1]
            verification = verify_event_chain(
                committed,
                expected_head_hash=manifest_event_hash,
                expected_run_id=run_id,
            )
            if (
                not verification.valid
                or not verification.truncation_checked
                or verification.event_count != len(committed)
                or verification.head_event_id != manifest_event_id
            ):
                raise EvidenceCommitIntegrityError(
                    "artifact-manifest recovery stream verification failed: "
                    f"{verification.to_dict()}"
                )
            streams[run_id] = committed
        return streams

    def _persist_checkpoints(
        self,
        request: EvidenceCommitRequest,
        event_streams: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> dict[str, PersistedLedgerCheckpoint]:
        ordered_run_ids = sorted(
            run_id
            for run_id in request.registered_run_ids
            if run_id != request.aggregate_run_id
        ) + [request.aggregate_run_id]
        persisted_checkpoints: dict[str, PersistedLedgerCheckpoint] = {}
        for index, run_id in enumerate(ordered_run_ids):
            events = list(event_streams[run_id])
            if not events:
                raise EvidenceCommitIntegrityError(
                    f"cannot checkpoint empty event stream: {run_id}"
                )
            head = events[-1]
            persisted = self.checkpoint_store.append_signed_head(
                run_id=run_id,
                head_event_id=head["event_id"],
                head_event_hash=str(head["event_hash"]),
                event_count=len(events),
                signer=self.signer,
                verifier=self.verifier,
                created_at=request.checkpoint_created_at + index,
            )
            persisted_checkpoints[run_id] = persisted
        return persisted_checkpoints

    def _persist_checkpoint_phase(
        self,
        request: EvidenceCommitRequest,
        event_streams: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        """Serialize cross-instance checkpoint publication and phase commit."""
        with self._lock, self._outbox_connection() as conn:
            _begin_outbox_transaction(conn)
            try:
                persisted = self._persist_checkpoints(
                    request,
                    event_streams,
                )
                trusted = self._pin_checkpoints(request, persisted)
                refs = {
                    run_id: checkpoint.external_anchor_ref
                    for run_id, checkpoint in persisted.items()
                }
                self._advance_in_transaction(
                    conn,
                    commit_id=request.commit_id,
                    phase="checkpoints_persisted",
                    detail={
                        "checkpoint_refs": refs,
                        "trusted_checkpoint_pins": trusted,
                    },
                    now=_now_ms(),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self._observe_phase("checkpoints_persisted")
        return refs, trusted

    def _persist_recovery_checkpoint_phase(
        self,
        request: EvidenceCommitRequest,
        recovery_streams: Mapping[
            str,
            Sequence[Mapping[str, Any]],
        ],
    ) -> tuple[
        dict[str, str],
        dict[str, dict[str, Any]],
        dict[str, list[dict[str, Any]]],
    ]:
        """Adopt or advance checkpoints without publishing a historical cut."""
        ordered_run_ids = sorted(
            run_id
            for run_id in request.registered_run_ids
            if run_id != request.aggregate_run_id
        ) + [request.aggregate_run_id]
        with self._lock, self._outbox_connection() as conn:
            _begin_outbox_transaction(conn)
            try:
                for _ in range(_MAX_RECOVERY_CHECKPOINT_REPLANS):
                    plans = {
                        run_id: self._plan_recovery_checkpoint(
                            run_id=run_id,
                            recovery_events=recovery_streams[run_id],
                        )
                        for run_id in ordered_run_ids
                    }
                    selected: dict[str, PersistedLedgerCheckpoint] = {}
                    for index, run_id in enumerate(ordered_run_ids):
                        plan = plans[run_id]
                        if plan.selected_existing is not None:
                            selected[run_id] = plan.selected_existing
                            continue
                        # The immutable projection may intentionally stay on an
                        # older cut, but the signed ledger checkpoint must cover
                        # the stable live snapshot observed during recovery.
                        head = plan.current_events[-1]
                        created_at = request.checkpoint_created_at + index
                        if plan.local_latest is not None:
                            created_at = max(
                                created_at,
                                int(
                                    plan.local_latest.checkpoint["predicate"][
                                        "created_at"
                                    ]
                                )
                                + 1,
                            )
                        selected[run_id] = (
                            self.checkpoint_store.append_signed_head(
                                run_id=run_id,
                                head_event_id=head["event_id"],
                                head_event_hash=str(head["event_hash"]),
                                event_count=len(plan.current_events),
                                signer=self.signer,
                                verifier=self.verifier,
                                created_at=created_at,
                            )
                        )

                    expected = {
                        run_id: checkpoint_identity(
                            selected[run_id].checkpoint
                        )
                        for run_id in ordered_run_ids
                    }
                    selected_streams: dict[
                        str,
                        list[dict[str, Any]],
                    ] = {}
                    replan = False
                    for run_id in ordered_run_ids:
                        plan = plans[run_id]
                        if self._recovery_live_snapshot_advanced(plan):
                            replan = True
                            break
                        identity = expected[run_id]
                        selected_streams[run_id] = (
                            self._checkpointed_event_prefix(
                                run_id=run_id,
                                events=plan.current_events,
                                identity=identity,
                                label="selected recovery checkpoint",
                            )
                        )
                        self._verify_recovery_checkpoint_identity(
                            run_id=run_id,
                            events=selected_streams[run_id],
                            identity=identity,
                            label="selected recovery checkpoint",
                            require_local_latest=False,
                        )
                        try:
                            local_latest = (
                                self.checkpoint_store.load_latest(run_id)
                            )
                        except RuntimeError as exc:
                            raise EvidenceCommitIntegrityError(
                                "local checkpoint history is invalid for "
                                f"{run_id}: {exc}"
                            ) from exc
                        local_identity = (
                            None
                            if local_latest is None
                            else checkpoint_identity(
                                local_latest.checkpoint
                            )
                        )
                        if self._checkpoint_snapshot_advanced(
                            earlier=identity,
                            later=local_identity,
                            run_id=run_id,
                            label="local checkpoint",
                        ):
                            replan = True
                            break
                    if replan:
                        continue

                    pin_store = self.trusted_checkpoint_pins
                    assert pin_store is not None
                    for run_id in sorted(expected):
                        try:
                            pin_store.pin(expected[run_id])
                        except Exception as exc:
                            observed = (
                                self._load_optional_trusted_latest_checkpoint(
                                    run_id
                                )
                            )
                            if self._checkpoint_snapshot_advanced(
                                earlier=expected[run_id],
                                later=observed,
                                run_id=run_id,
                                label="trusted checkpoint",
                            ):
                                replan = True
                                break
                            raise EvidenceCommitIntegrityError(
                                "trusted checkpoint pin publication failed "
                                f"for {run_id}: {exc}"
                            ) from exc
                    if replan:
                        continue

                    trusted: dict[str, dict[str, Any]] = {}
                    for run_id in sorted(expected):
                        latest = (
                            self._load_optional_trusted_latest_checkpoint(
                                run_id
                            )
                        )
                        if self._checkpoint_snapshot_advanced(
                            earlier=expected[run_id],
                            later=latest,
                            run_id=run_id,
                            label="trusted checkpoint",
                        ):
                            replan = True
                            break
                        assert latest is not None
                        trusted[run_id] = latest
                        try:
                            local_latest = (
                                self.checkpoint_store.load_latest(run_id)
                            )
                        except RuntimeError as exc:
                            raise EvidenceCommitIntegrityError(
                                "local checkpoint history is invalid for "
                                f"{run_id}: {exc}"
                            ) from exc
                        local_identity = (
                            None
                            if local_latest is None
                            else checkpoint_identity(
                                local_latest.checkpoint
                            )
                        )
                        if self._checkpoint_snapshot_advanced(
                            earlier=expected[run_id],
                            later=local_identity,
                            run_id=run_id,
                            label="local checkpoint",
                        ):
                            replan = True
                            break
                        if self._recovery_live_snapshot_advanced(plans[run_id]):
                            replan = True
                            break
                    if replan:
                        continue

                    refs = {
                        run_id: checkpoint.external_anchor_ref
                        for run_id, checkpoint in selected.items()
                    }
                    self._advance_in_transaction(
                        conn,
                        commit_id=request.commit_id,
                        phase="checkpoints_persisted",
                        detail={
                            "recovery_checkpoint_selection_schema": (
                                _RECOVERY_CHECKPOINT_SELECTION_SCHEMA_VERSION
                            ),
                            "checkpoint_refs": refs,
                            "trusted_checkpoint_pins": trusted,
                        },
                        now=_now_ms(),
                    )
                    conn.commit()
                    break
                else:
                    raise EvidenceCommitIntegrityError(
                        "recovery checkpoint publication did not stabilize "
                        "after monotonic advancement"
                    )
            except Exception:
                conn.rollback()
                raise
        self._observe_phase("checkpoints_persisted")
        return refs, trusted, selected_streams

    def _plan_recovery_checkpoint(
        self,
        *,
        run_id: str,
        recovery_events: Sequence[Mapping[str, Any]],
    ) -> _RecoveryCheckpointPlan:
        recovery = tuple(dict(event) for event in recovery_events)
        if not recovery:
            raise EvidenceCommitIntegrityError(
                f"cannot recover an empty checkpoint stream: {run_id}"
            )
        for _ in range(_MAX_RECOVERY_CHECKPOINT_REPLANS):
            trusted_before = self._load_optional_trusted_latest_checkpoint(
                run_id
            )
            try:
                checkpoint_history = self.checkpoint_store.load_all(run_id)
            except RuntimeError as exc:
                raise EvidenceCommitIntegrityError(
                    f"local checkpoint history is invalid for "
                    f"{run_id}: {exc}"
                ) from exc
            current = tuple(self._read_all_events(run_id))
            trusted_latest = (
                self._load_optional_trusted_latest_checkpoint(run_id)
            )
            if self._checkpoint_snapshot_advanced(
                earlier=trusted_before,
                later=trusted_latest,
                run_id=run_id,
                label="trusted checkpoint planning snapshot",
            ):
                continue
            if len(current) < len(recovery):
                raise EvidenceCommitIntegrityError(
                    "live evidence stream was rolled back below the "
                    f"immutable recovery cut for {run_id}"
                )
            self._assert_recovery_checkpoint_prefix(
                run_id=run_id,
                recovery_events=recovery,
                checkpoint_events=current,
                label="live event stream",
            )
            local_latest = (
                checkpoint_history[-1] if checkpoint_history else None
            )
            if trusted_latest is not None:
                trusted_checkpoint = next(
                    (
                        checkpoint
                        for checkpoint in checkpoint_history
                        if checkpoint_identity(checkpoint.checkpoint)
                        == trusted_latest
                    ),
                    None,
                )
                if trusted_checkpoint is None:
                    raise EvidenceCommitIntegrityError(
                        "trusted latest checkpoint is missing locally for "
                        f"{run_id}"
                    )
                trusted_events = self._checkpointed_event_prefix(
                    run_id=run_id,
                    events=current,
                    identity=trusted_latest,
                    label="trusted latest checkpoint",
                )
                self._verify_recovery_checkpoint_identity(
                    run_id=run_id,
                    events=trusted_events,
                    identity=trusted_latest,
                    label="trusted latest checkpoint",
                    require_local_latest=False,
                )
                self._assert_recovery_checkpoint_prefix(
                    run_id=run_id,
                    recovery_events=recovery,
                    checkpoint_events=trusted_events,
                    label="trusted latest checkpoint",
                )

            selected_existing: PersistedLedgerCheckpoint | None = None
            if local_latest is not None:
                local_identity = checkpoint_identity(
                    local_latest.checkpoint
                )
                local_events = self._checkpointed_event_prefix(
                    run_id=run_id,
                    events=current,
                    identity=local_identity,
                    label="local latest checkpoint",
                )
                self._verify_recovery_checkpoint_identity(
                    run_id=run_id,
                    events=local_events,
                    identity=local_identity,
                    label="local latest checkpoint",
                    require_local_latest=False,
                )
                self._assert_recovery_checkpoint_prefix(
                    run_id=run_id,
                    recovery_events=recovery,
                    checkpoint_events=local_events,
                    label="local latest checkpoint",
                )
                if trusted_latest is not None:
                    trusted_count = int(trusted_latest["event_count"])
                    local_count = int(local_identity["event_count"])
                    if local_count < trusted_count or (
                        local_count == trusted_count
                        and local_identity != trusted_latest
                    ):
                        raise EvidenceCommitIntegrityError(
                            "local latest checkpoint rolled back or forked "
                            "before the trusted latest checkpoint for "
                            f"{run_id}"
                        )
                if len(local_events) >= len(current):
                    selected_existing = local_latest

            return _RecoveryCheckpointPlan(
                run_id=run_id,
                recovery_events=recovery,
                current_events=current,
                local_latest=local_latest,
                selected_existing=selected_existing,
            )
        raise EvidenceCommitIntegrityError(
            "recovery checkpoint planning snapshot did not stabilize for "
            f"{run_id}"
        )

    def _checkpoint_snapshot_advanced(
        self,
        *,
        earlier: Mapping[str, Any] | None,
        later: Mapping[str, Any] | None,
        run_id: str,
        label: str,
    ) -> bool:
        if earlier is None:
            return later is not None
        if later is None:
            raise EvidenceCommitIntegrityError(
                f"{label} rolled back or disappeared for {run_id}"
            )
        normalized_earlier = normalize_checkpoint_identity(earlier)
        normalized_later = normalize_checkpoint_identity(later)
        earlier_count = int(normalized_earlier["event_count"])
        later_count = int(normalized_later["event_count"])
        if later_count > earlier_count:
            return True
        if later_count < earlier_count:
            raise EvidenceCommitIntegrityError(
                f"{label} rolled back for {run_id}"
            )
        if normalized_later != normalized_earlier:
            raise EvidenceCommitIntegrityError(
                f"{label} forked at event_count={later_count} for {run_id}"
            )
        return False

    def _recovery_live_snapshot_advanced(
        self,
        plan: _RecoveryCheckpointPlan,
    ) -> bool:
        observed = tuple(self._read_all_events(plan.run_id))
        if len(observed) < len(plan.current_events):
            raise EvidenceCommitIntegrityError(
                "live evidence stream was rolled back below the stable "
                f"recovery snapshot for {plan.run_id}"
            )
        self._assert_recovery_checkpoint_prefix(
            run_id=plan.run_id,
            recovery_events=plan.current_events,
            checkpoint_events=observed,
            label="live recovery snapshot",
        )
        return len(observed) > len(plan.current_events)

    def _load_optional_trusted_latest_checkpoint(
        self,
        run_id: str,
    ) -> dict[str, Any] | None:
        pin_store = self.trusted_checkpoint_pins
        assert pin_store is not None

        def read_latest() -> dict[str, Any] | None:
            try:
                observed = pin_store.latest(run_id)
            except Exception as exc:
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint lookup failed for "
                    f"{run_id}: {exc}"
                ) from exc
            if observed is None:
                return None
            try:
                normalized = normalize_checkpoint_identity(observed)
            except (TypeError, ValueError, RuntimeError) as exc:
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint is invalid for "
                    f"{run_id}: {exc}"
                ) from exc
            if normalized["run_id"] != run_id:
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint run_id mismatch for "
                    f"{run_id}"
                )
            return normalized

        for _ in range(_MAX_RECOVERY_CHECKPOINT_REPLANS):
            normalized = read_latest()
            if normalized is None:
                confirmed = read_latest()
                if self._checkpoint_snapshot_advanced(
                    earlier=None,
                    later=confirmed,
                    run_id=run_id,
                    label="trusted checkpoint lookup",
                ):
                    continue
                return None
            try:
                persisted = pin_store.get(normalized)
            except Exception as exc:
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint persistence lookup failed for "
                    f"{run_id}: {exc}"
                ) from exc
            if persisted is None:
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint pin is not durably retrievable "
                    f"for {run_id}"
                )
            try:
                normalized_persisted = normalize_checkpoint_identity(
                    persisted
                )
            except (TypeError, ValueError, RuntimeError) as exc:
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint pin is invalid for "
                    f"{run_id}: {exc}"
                ) from exc
            if normalized_persisted != normalized:
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint pin differs from latest for "
                    f"{run_id}"
                )
            confirmed = read_latest()
            if self._checkpoint_snapshot_advanced(
                earlier=normalized,
                later=confirmed,
                run_id=run_id,
                label="trusted checkpoint lookup",
            ):
                continue
            return normalized
        raise EvidenceCommitIntegrityError(
            "trusted latest checkpoint lookup did not stabilize for "
            f"{run_id}"
        )

    def _checkpointed_event_prefix(
        self,
        *,
        run_id: str,
        events: Sequence[Mapping[str, Any]],
        identity: Mapping[str, Any],
        label: str,
    ) -> list[dict[str, Any]]:
        event_count = int(identity["event_count"])
        if len(events) < event_count:
            raise EvidenceCommitIntegrityError(
                f"{label} advances beyond the local event stream for "
                f"{run_id}"
            )
        return [dict(event) for event in events[:event_count]]

    def _verify_recovery_checkpoint_identity(
        self,
        *,
        run_id: str,
        events: Sequence[Mapping[str, Any]],
        identity: Mapping[str, Any],
        label: str,
        require_local_latest: bool,
    ) -> None:
        verification = verify_authoritative_event_chain(
            events,
            expected_run_id=run_id,
            checkpoint_store=self.checkpoint_store,
            verifier=self.verifier,
            trusted_latest_checkpoint=identity,
            require_local_latest=require_local_latest,
        )
        if (
            not verification.valid
            or not verification.truncation_checked
            or not verification.authoritative_head_verified
        ):
            raise EvidenceCommitIntegrityError(
                f"{label} verification failed for "
                f"{run_id}: {verification.to_dict()}"
            )

    def _assert_recovery_checkpoint_prefix(
        self,
        *,
        run_id: str,
        recovery_events: Sequence[Mapping[str, Any]],
        checkpoint_events: Sequence[Mapping[str, Any]],
        label: str,
    ) -> None:
        shared_count = min(len(recovery_events), len(checkpoint_events))
        if shared_count == 0:
            raise EvidenceCommitIntegrityError(
                f"{label} has no event prefix for {run_id}"
            )
        for index in range(shared_count):
            if canonical_json_bytes(dict(recovery_events[index])) != (
                canonical_json_bytes(dict(checkpoint_events[index]))
            ):
                raise EvidenceCommitIntegrityError(
                    f"{label} prefix mismatch or fork for {run_id} at "
                    f"event offset {index + 1}"
                )

    def _pin_checkpoints(
        self,
        request: EvidenceCommitRequest,
        persisted_checkpoints: Mapping[str, PersistedLedgerCheckpoint],
    ) -> dict[str, dict[str, Any]]:
        pin_store = self.trusted_checkpoint_pins
        assert pin_store is not None
        expected = {
            run_id: checkpoint_identity(
                persisted_checkpoints[run_id].checkpoint
            )
            for run_id in request.registered_run_ids
        }
        for run_id in sorted(expected):
            try:
                pin_store.pin(expected[run_id])
            except Exception as exc:
                raise EvidenceCommitIntegrityError(
                    "trusted checkpoint pin publication failed for "
                    f"{run_id}: {exc}"
                ) from exc
        return self._load_trusted_checkpoint_pins(expected)

    def _load_trusted_checkpoint_pins(
        self,
        expected: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        pin_store = self.trusted_checkpoint_pins
        assert pin_store is not None
        trusted: dict[str, dict[str, Any]] = {}
        for run_id, expected_identity in expected.items():
            normalized_expected = normalize_checkpoint_identity(
                expected_identity
            )
            try:
                observed = pin_store.get(normalized_expected)
            except Exception as exc:
                raise EvidenceCommitIntegrityError(
                    "trusted checkpoint pin lookup failed for "
                    f"{run_id}: {exc}"
                ) from exc
            if observed is None:
                raise EvidenceCommitIntegrityError(
                    "trusted checkpoint pin is missing for "
                    f"{run_id}"
                )
            try:
                normalized_observed = normalize_checkpoint_identity(
                    observed
                )
            except (TypeError, ValueError, RuntimeError) as exc:
                raise EvidenceCommitIntegrityError(
                    "trusted checkpoint pin is invalid for "
                    f"{run_id}: {exc}"
                ) from exc
            if normalized_observed != normalized_expected:
                raise EvidenceCommitIntegrityError(
                    "trusted checkpoint pin differs from the committed head "
                    f"for {run_id}"
                )
            trusted[run_id] = normalized_observed
        return trusted

    def _checkpoint_pin_mapping(
        self,
        value: Any,
        *,
        run_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(value, Mapping) or set(value) != set(run_ids):
            raise EvidenceCommitIntegrityError(
                "completed evidence result lacks exact trusted checkpoint pins"
            )
        pins: dict[str, dict[str, Any]] = {}
        for run_id in run_ids:
            identity = value.get(run_id)
            if not isinstance(identity, Mapping):
                raise EvidenceCommitIntegrityError(
                    f"completed evidence trusted pin is invalid for {run_id}"
                )
            try:
                normalized = normalize_checkpoint_identity(identity)
            except (TypeError, ValueError, RuntimeError) as exc:
                raise EvidenceCommitIntegrityError(
                    f"completed evidence trusted pin is invalid for "
                    f"{run_id}: {exc}"
                ) from exc
            if normalized["run_id"] != run_id:
                raise EvidenceCommitIntegrityError(
                    "completed evidence trusted pin run_id mismatch for "
                    f"{run_id}"
                )
            pins[run_id] = normalized
        return pins

    def _load_checkpointed_recovery(
        self,
        *,
        request: EvidenceCommitRequest,
        detail: Mapping[str, Any],
        expected_streams: Mapping[
            str,
            Sequence[Mapping[str, Any]],
        ],
    ) -> tuple[
        dict[str, str],
        dict[str, dict[str, Any]],
        dict[str, list[dict[str, Any]]],
    ]:
        """Reload an already-published checkpoint cut without re-signing it."""
        recovery_selection_schema = detail.get(
            "recovery_checkpoint_selection_schema"
        )
        if recovery_selection_schema not in {
            None,
            _RECOVERY_CHECKPOINT_SELECTION_SCHEMA_VERSION,
        }:
            raise EvidenceCommitIntegrityError(
                "checkpointed recovery selection schema is invalid"
            )
        allows_covering_checkpoint = (
            recovery_selection_schema
            == _RECOVERY_CHECKPOINT_SELECTION_SCHEMA_VERSION
        )
        trusted_checkpoint_pins = self._checkpoint_pin_mapping(
            detail.get("trusted_checkpoint_pins"),
            run_ids=request.registered_run_ids,
        )
        trusted_checkpoint_pins = self._load_trusted_checkpoint_pins(
            trusted_checkpoint_pins
        )
        pinned_streams = self._read_pinned_event_streams(
            trusted_checkpoint_pins
        )
        for run_id in request.registered_run_ids:
            expected_events = [
                dict(event) for event in expected_streams[run_id]
            ]
            if not expected_events:
                raise EvidenceCommitIntegrityError(
                    f"checkpointed evidence stream is empty for {run_id}"
                )
            events = pinned_streams[run_id]
            identity = trusted_checkpoint_pins[run_id]
            if allows_covering_checkpoint:
                if len(events) < len(expected_events):
                    raise EvidenceCommitIntegrityError(
                        "checkpointed evidence pin does not cover the "
                        f"immutable recovery cut for {run_id}"
                    )
                self._assert_recovery_checkpoint_prefix(
                    run_id=run_id,
                    recovery_events=expected_events,
                    checkpoint_events=events,
                    label="checkpointed recovery selection",
                )
            else:
                head = expected_events[-1]
                if (
                    int(identity["event_count"]) != len(expected_events)
                    or int(identity["head_event_id"])
                    != int(head["event_id"])
                    or str(identity["head_event_hash"])
                    != str(head["event_hash"])
                ):
                    raise EvidenceCommitIntegrityError(
                        "checkpointed evidence pin differs from the immutable "
                        f"recovery cut for {run_id}"
                    )
        checkpoint_refs = self._checkpoint_ref_mapping(
            detail.get("checkpoint_refs"),
            run_ids=request.registered_run_ids,
        )
        expected_refs = {
            run_id: str(identity["external_anchor_ref"])
            for run_id, identity in trusted_checkpoint_pins.items()
        }
        if checkpoint_refs != expected_refs:
            raise EvidenceCommitIntegrityError(
                "checkpointed evidence refs differ from trusted pins"
            )
        trusted_latest_checkpoint_pins = (
            self._load_latest_trusted_checkpoint_pins(
                run_ids=request.registered_run_ids,
                committed=trusted_checkpoint_pins,
            )
        )
        self._verify_trusted_latest_checkpoint_cuts(
            trusted_latest_checkpoint_pins
        )
        return (
            checkpoint_refs,
            trusted_checkpoint_pins,
            pinned_streams,
        )

    def _load_latest_trusted_checkpoint_pins(
        self,
        *,
        run_ids: Sequence[str],
        committed: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        pin_store = self.trusted_checkpoint_pins
        assert pin_store is not None
        latest: dict[str, dict[str, Any]] = {}
        for run_id in run_ids:
            try:
                observed = pin_store.latest(run_id)
            except Exception as exc:
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint lookup failed for "
                    f"{run_id}: {exc}"
                ) from exc
            if observed is None:
                raise EvidenceCommitIntegrityError(
                    f"trusted latest checkpoint is missing for {run_id}"
                )
            try:
                normalized = normalize_checkpoint_identity(observed)
            except (TypeError, ValueError, RuntimeError) as exc:
                raise EvidenceCommitIntegrityError(
                    f"trusted latest checkpoint is invalid for {run_id}: {exc}"
                ) from exc
            if normalized["run_id"] != run_id:
                raise EvidenceCommitIntegrityError(
                    f"trusted latest checkpoint run_id mismatch for {run_id}"
                )
            committed_identity = normalize_checkpoint_identity(committed[run_id])
            latest_count = int(normalized["event_count"])
            committed_count = int(committed_identity["event_count"])
            if latest_count < committed_count or (
                latest_count == committed_count
                and normalized != committed_identity
            ):
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint rolled back or forked before "
                    f"the committed cut for {run_id}"
                )
            latest[run_id] = normalized
        return latest

    def _checkpoint_ref_mapping(
        self,
        value: Any,
        *,
        run_ids: Sequence[str],
    ) -> dict[str, str]:
        if not isinstance(value, Mapping) or set(value) != set(run_ids):
            raise EvidenceCommitIntegrityError(
                "completed evidence result lacks exact checkpoint refs"
            )
        refs = {
            run_id: str(value.get(run_id) or "")
            for run_id in run_ids
        }
        if any(not ref for ref in refs.values()):
            raise EvidenceCommitIntegrityError(
                "completed evidence result contains an empty checkpoint ref"
            )
        return refs

    def _read_pinned_event_streams(
        self,
        trusted_checkpoint_pins: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        streams: dict[str, list[dict[str, Any]]] = {}
        for run_id, identity in trusted_checkpoint_pins.items():
            event_count = int(identity["event_count"])
            events = self._read_all_events(run_id)
            if len(events) < event_count:
                raise EvidenceCommitIntegrityError(
                    "pinned evidence stream was rolled back below its "
                    f"committed cut: {run_id}"
                )
            streams[run_id] = events[:event_count]
        return streams

    def _verify_trusted_latest_checkpoint_cuts(
        self,
        trusted_latest_checkpoint_pins: Mapping[str, Mapping[str, Any]],
    ) -> None:
        for run_id, identity in trusted_latest_checkpoint_pins.items():
            event_count = int(identity["event_count"])
            events = self._read_all_events(run_id)
            if len(events) < event_count:
                raise EvidenceCommitIntegrityError(
                    "event stream was rolled back below the externally pinned "
                    f"latest checkpoint: {run_id}"
                )
            verification = verify_authoritative_event_chain(
                events[:event_count],
                expected_run_id=run_id,
                checkpoint_store=self.checkpoint_store,
                verifier=self.verifier,
                trusted_latest_checkpoint=identity,
            )
            if (
                not verification.valid
                or not verification.truncation_checked
                or not verification.authoritative_head_verified
            ):
                raise EvidenceCommitIntegrityError(
                    "trusted latest checkpoint verification failed for "
                    f"{run_id}: {verification.to_dict()}"
                )

    def _verify_authoritatively(
        self,
        *,
        request: EvidenceCommitRequest,
        materialization: Mapping[str, Any],
        graph: TraceGraph,
        closure: ClosureResult,
        manifest_event: Mapping[str, Any],
        precommit_heads: Mapping[str, Any],
        event_streams: Mapping[str, Sequence[Mapping[str, Any]]],
        trusted_checkpoint_pins: Mapping[str, Mapping[str, Any]],
        require_local_latest: bool = True,
        record_phase: bool = True,
    ) -> tuple[dict[str, LedgerVerification], dict[str, Any]]:
        verifications: dict[str, LedgerVerification] = {}
        aggregate_events: list[dict[str, Any]] | None = None
        for run_id in request.registered_run_ids:
            events = [dict(event) for event in event_streams[run_id]]
            verification = verify_authoritative_event_chain(
                events,
                expected_run_id=run_id,
                checkpoint_store=self.checkpoint_store,
                verifier=self.verifier,
                trusted_latest_checkpoint=trusted_checkpoint_pins[run_id],
                require_local_latest=require_local_latest,
            )
            if (
                not verification.valid
                or not verification.truncation_checked
                or not verification.authoritative_head_verified
            ):
                raise EvidenceCommitIntegrityError(
                    "authoritative ledger verification failed for "
                    f"{run_id}: {verification.to_dict()}"
                )
            verifications[run_id] = verification
            if run_id == request.aggregate_run_id:
                aggregate_events = events
        assert aggregate_events is not None
        manifest_indexes = [
            index
            for index, event in enumerate(aggregate_events)
            if int(event["event_id"]) == int(manifest_event["event_id"])
        ]
        if len(manifest_indexes) != 1:
            raise EvidenceCommitIntegrityError(
                "authoritative aggregate checkpoint does not contain the "
                "exact manifest event"
            )
        manifest_index = manifest_indexes[0]
        checkpointed_manifest = aggregate_events[manifest_index]
        if canonical_json_bytes(checkpointed_manifest) != (
            canonical_json_bytes(dict(manifest_event))
        ):
            raise EvidenceCommitIntegrityError(
                "authoritative aggregate checkpoint manifest event differs "
                "from the committed manifest"
            )
        aggregate_precommit = precommit_heads[request.aggregate_run_id]
        projection_event_count = int(aggregate_precommit["event_count"])
        if (
            len(aggregate_events) < projection_event_count
            or manifest_index < projection_event_count
        ):
            raise EvidenceCommitIntegrityError(
                "authoritative aggregate checkpoint does not preserve the "
                "frozen precommit projection cut"
            )
        projection_events = aggregate_events[:projection_event_count]
        projection_head = projection_events[-1]
        if (
            int(projection_head["event_id"])
            != int(aggregate_precommit["head_event_id"])
            or str(projection_head["event_hash"])
            != str(aggregate_precommit["head_event_hash"])
        ):
            raise EvidenceCommitIntegrityError(
                "authoritative aggregate checkpoint differs from the frozen "
                "precommit projection head"
            )
        aggregate_verification = verifications[request.aggregate_run_id]
        projection = rebuild_projection(
            projection_events,
            initial=initial_tracer_evidence_projection(
                request.aggregate_run_id
            ),
            reducer=reduce_tracer_evidence_projection,
            expected_head_hash=str(
                aggregate_precommit["head_event_hash"]
            ),
            expected_run_id=request.aggregate_run_id,
        )
        self._validate_projection(projection, request)
        projection_bytes = canonical_json_bytes(projection)
        projection_hash = sha256_hex(projection_bytes)
        if projection_hash != materialization["projection_sha256"]:
            raise EvidenceCommitIntegrityError(
                "authoritative tracer projection checksum mismatch"
            )
        projection_descriptor = next(
            descriptor
            for descriptor in materialization["artifacts"]
            if descriptor["role"] == "tracer_projection"
        )
        persisted_projection = self.artifact_store.read_bytes(
            str(projection_descriptor["digest"]["sha256"])
        )
        if (
            persisted_projection != projection_bytes
            or _read_evidence_file(
                self.root,
                str(projection_descriptor["ref"]),
            )
            != projection_bytes
        ):
            raise EvidenceCommitIntegrityError(
                "authoritative tracer projection bytes differ from persistence"
            )
        trace_descriptor = next(
            descriptor
            for descriptor in materialization["artifacts"]
            if descriptor["role"] == "trace_graph"
        )
        if self.artifact_store.read_bytes(
            str(trace_descriptor["digest"]["sha256"])
        ) != canonical_json_bytes(
            {
                "graph": graph.to_dict(),
                "closure": closure.to_dict(),
            }
        ):
            raise EvidenceCommitIntegrityError(
                "authenticated trace artifact differs from persisted graph"
            )
        if record_phase:
            self._advance(
                request.commit_id,
                "authoritatively_verified",
                {
                    "run_count": len(verifications),
                    "aggregate_head_event_hash": (
                        aggregate_verification.head_event_hash
                    ),
                    "projection_sha256": projection_hash,
                },
            )
        return verifications, projection

    def _validate_projection(
        self,
        projection: Mapping[str, Any],
        request: EvidenceCommitRequest,
    ) -> None:
        executions = projection.get("executions")
        matrix = projection.get("matrix")
        trace = projection.get("trace")
        claim = projection.get("claim")
        completion = projection.get("completion")
        if (
            projection.get("mode") != request.mode
            or projection.get("operational_efficacy_evidence") is not False
            or not isinstance(executions, Mapping)
            or not isinstance(matrix, list)
            or len(matrix) != len(executions)
            or len(executions) != len(request.grade_histories)
            or not isinstance(trace, Mapping)
            or trace.get("status") != "accepted"
            or not isinstance(claim, Mapping)
            or claim.get("max_claim_level") != request.claim_cap
            or claim.get("operational_efficacy_evidence") is not False
            or claim.get("improvement_claim_allowed") is not False
            or not isinstance(completion, Mapping)
            or completion.get("execution_count") != len(executions)
            or completion.get("claim_cap") != request.claim_cap
            or completion.get("mode") != request.mode
            or completion.get("external_provider_calls") != 0
        ):
            raise EvidenceCommitIntegrityError(
                "tracer projection violates the frozen hermetic contract"
            )

    def _capture_heads(
        self,
        run_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        heads: dict[str, dict[str, Any]] = {}
        for run_id in run_ids:
            events = self._read_all_events(run_id)
            if not events:
                raise EvidenceCommitIntegrityError(
                    f"registered evidence run has no events: {run_id}"
                )
            head = events[-1]
            heads[str(run_id)] = {
                "event_count": len(events),
                "head_event_id": int(head["event_id"]),
                "head_event_hash": str(head["event_hash"]),
            }
        return heads

    def _assert_precommit_streams(
        self,
        *,
        request: EvidenceCommitRequest,
        precommit_heads: Mapping[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        streams = {
            run_id: self._read_all_events(run_id)
            for run_id in request.registered_run_ids
        }
        if (
            self._find_manifest_event_in(
                streams[request.aggregate_run_id],
                request,
            )
            is not None
        ):
            raise EvidenceCommitIntegrityError(
                "manifest event exists before the manifest phase was reconciled"
            )
        for run_id in request.registered_run_ids:
            self._assert_head_matches_events(
                run_id,
                streams[run_id],
                precommit_heads[run_id],
            )
        return streams

    def _verify_precommit_streams(
        self,
        *,
        request: EvidenceCommitRequest,
        precommit_heads: Mapping[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        """Verify the frozen cut before signing its detached manifest."""
        streams = self._assert_precommit_streams(
            request=request,
            precommit_heads=precommit_heads,
        )
        for run_id in request.registered_run_ids:
            expected = precommit_heads[run_id]
            events = streams[run_id]
            verification = verify_event_chain(
                events,
                expected_head_hash=str(expected["head_event_hash"]),
                expected_run_id=run_id,
            )
            if (
                not verification.valid
                or not verification.truncation_checked
                or verification.event_count != int(expected["event_count"])
                or verification.head_event_id
                != int(expected["head_event_id"])
            ):
                raise EvidenceCommitIntegrityError(
                    "frozen evidence stream verification failed for "
                    f"{run_id}: {verification.to_dict()}"
                )
        return streams

    def _verify_append_only_event_suffixes(
        self,
        *,
        request: EvidenceCommitRequest,
        precommit_heads: Mapping[str, Any],
    ) -> None:
        """Require every live stream to preserve and extend its frozen prefix."""
        for run_id in request.registered_run_ids:
            events = self._read_all_events(run_id)
            self._verify_event_prefix(
                run_id=run_id,
                events=events,
                expected=precommit_heads[run_id],
            )
            verification = verify_event_chain(
                events,
                expected_head_hash=str(events[-1]["event_hash"]),
                expected_run_id=run_id,
            )
            if (
                not verification.valid
                or not verification.truncation_checked
                or verification.event_count != len(events)
                or verification.head_event_id != int(events[-1]["event_id"])
            ):
                raise EvidenceCommitIntegrityError(
                    "append-only evidence suffix verification failed for "
                    f"{run_id}: {verification.to_dict()}"
                )

    def _verify_event_prefix(
        self,
        *,
        run_id: str,
        events: Sequence[Mapping[str, Any]],
        expected: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        event_count = int(expected["event_count"])
        if len(events) < event_count:
            raise EvidenceCommitIntegrityError(
                f"evidence stream rolled back below its immutable prefix: "
                f"{run_id}"
            )
        prefix = [dict(event) for event in events[:event_count]]
        if not prefix:
            raise EvidenceCommitIntegrityError(
                f"evidence stream disappeared: {run_id}"
            )
        head = prefix[-1]
        if (
            int(head["event_id"]) != int(expected["head_event_id"])
            or str(head["event_hash"]) != str(expected["head_event_hash"])
        ):
            raise EvidenceCommitIntegrityError(
                f"evidence stream immutable prefix changed: {run_id}"
            )
        verification = verify_event_chain(
            prefix,
            expected_head_hash=str(expected["head_event_hash"]),
            expected_run_id=run_id,
        )
        if (
            not verification.valid
            or not verification.truncation_checked
            or verification.event_count != event_count
            or verification.head_event_id != int(expected["head_event_id"])
        ):
            raise EvidenceCommitIntegrityError(
                "immutable evidence prefix verification failed for "
                f"{run_id}: {verification.to_dict()}"
            )
        return prefix

    def _assert_head_matches(
        self,
        run_id: str,
        expected: Mapping[str, Any],
    ) -> None:
        self._assert_head_matches_events(
            run_id,
            self._read_all_events(run_id),
            expected,
        )

    def _assert_head_matches_events(
        self,
        run_id: str,
        events: Sequence[Mapping[str, Any]],
        expected: Mapping[str, Any],
    ) -> None:
        if not events:
            raise EvidenceCommitIntegrityError(
                f"evidence stream disappeared: {run_id}"
            )
        head = events[-1]
        if (
            len(events) != int(expected["event_count"])
            or int(head["event_id"]) != int(expected["head_event_id"])
            or str(head["event_hash"]) != str(expected["head_event_hash"])
        ):
            raise EvidenceCommitIntegrityError(
                f"evidence stream changed during commit: {run_id}"
            )

    def _find_manifest_event(
        self,
        request: EvidenceCommitRequest,
    ) -> dict[str, Any] | None:
        return self._find_manifest_event_in(
            self._read_all_events(request.aggregate_run_id),
            request,
        )

    def _find_manifest_event_in(
        self,
        events: Sequence[Mapping[str, Any]],
        request: EvidenceCommitRequest,
    ) -> dict[str, Any] | None:
        matches = [
            event
            for event in events
            if event["kind"] == EVIDENCE_COMMIT_EVENT_KIND
            and isinstance(event.get("payload"), Mapping)
            and event["payload"].get("commit_id") == request.commit_id
        ]
        if len(matches) > 1:
            raise EvidenceCommitIntegrityError(
                "duplicate artifact-manifest events exist for one commit"
            )
        return matches[0] if matches else None

    def _read_all_events(self, run_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        cursor = 0
        while True:
            page = self.state.read_events_since(
                run_id,
                after_event_id=cursor,
                limit=1000,
            )
            if not page:
                return events
            events.extend(page)
            cursor = int(page[-1]["event_id"])

    def _store_materialization(
        self,
        commit_id: str,
        materialization: Mapping[str, Any],
    ) -> None:
        detail = {
            "artifact_count": len(materialization["artifacts"]),
            "artifact_manifest_hash": materialization[
                "artifact_manifest"
            ]["manifest_hash"],
            "artifact_manifest_attestation": materialization[
                "artifact_manifest_attestation_identity"
            ],
            "trace_lifecycle": materialization["trace_lifecycle"],
            "projection_sha256": materialization["projection_sha256"],
        }
        now = _now_ms()
        value = canonical_json_bytes(materialization).decode("utf-8")
        with self._lock, self._outbox_connection() as conn:
            _begin_outbox_transaction(conn)
            row = conn.execute(
                """
                SELECT materialization_json, phase
                  FROM evidence_commits
                 WHERE commit_id=?
                """,
                (commit_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise EvidenceCommitIntegrityError(
                    "evidence outbox row disappeared"
                )
            existing = row["materialization_json"]
            if existing is not None and str(existing) != value:
                conn.rollback()
                raise EvidenceCommitConflict(
                    "evidence materialization changed during replay"
                )
            conn.execute(
                """
                UPDATE evidence_commits
                   SET materialization_json=?, status='in_progress',
                       phase='artifacts_staged', updated_at_ms=?
                 WHERE commit_id=?
                """,
                (value, now, commit_id),
            )
            self._insert_or_verify_phase(
                conn,
                commit_id=commit_id,
                phase="artifacts_staged",
                detail=detail,
                now=now,
            )
            conn.commit()

    def _advance(
        self,
        commit_id: str,
        phase: str,
        detail: Mapping[str, Any],
    ) -> None:
        if phase not in _PHASE_INDEX:
            raise ValueError(f"unknown evidence commit phase: {phase}")
        now = _now_ms()
        with self._lock, self._outbox_connection() as conn:
            _begin_outbox_transaction(conn)
            self._advance_in_transaction(
                conn,
                commit_id=commit_id,
                phase=phase,
                detail=detail,
                now=now,
            )
            conn.commit()
        self._observe_phase(phase)

    def _advance_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        commit_id: str,
        phase: str,
        detail: Mapping[str, Any],
        now: int,
    ) -> None:
        if phase not in _PHASE_INDEX:
            raise ValueError(f"unknown evidence commit phase: {phase}")
        row = conn.execute(
            "SELECT phase FROM evidence_commits WHERE commit_id=?",
            (commit_id,),
        ).fetchone()
        if row is None:
            raise EvidenceCommitIntegrityError(
                "evidence outbox row disappeared"
            )
        current = str(row["phase"])
        self._insert_or_verify_phase(
            conn,
            commit_id=commit_id,
            phase=phase,
            detail=detail,
            now=now,
        )
        if _PHASE_INDEX[phase] > _PHASE_INDEX[current]:
            conn.execute(
                """
                UPDATE evidence_commits
                   SET phase=?, status='in_progress', updated_at_ms=?
                 WHERE commit_id=?
                """,
                (phase, now, commit_id),
            )

    def _complete(
        self,
        commit_id: str,
        result: Mapping[str, Any],
    ) -> None:
        now = _now_ms()
        with self._lock, self._outbox_connection() as conn:
            _begin_outbox_transaction(conn)
            self._insert_or_verify_phase(
                conn,
                commit_id=commit_id,
                phase="complete",
                detail=result,
                now=now,
            )
            conn.execute(
                """
                UPDATE evidence_commits
                   SET phase='complete', status='complete', result_json=?,
                       error_json=NULL, updated_at_ms=?
                 WHERE commit_id=?
                """,
                (
                    canonical_json_bytes(result).decode("utf-8"),
                    now,
                    commit_id,
                ),
            )
            conn.commit()
        self._observe_phase("complete")

    def _mark_failed(self, commit_id: str, error: Exception) -> None:
        try:
            with self._lock, self._outbox_connection() as conn:
                row = conn.execute(
                    "SELECT status FROM evidence_commits WHERE commit_id=?",
                    (commit_id,),
                ).fetchone()
                if row is None or row["status"] == "complete":
                    return
                conn.execute(
                    """
                    UPDATE evidence_commits
                       SET status='failed', error_json=?, updated_at_ms=?
                     WHERE commit_id=?
                    """,
                    (
                        canonical_json_bytes(
                            {
                                "type": type(error).__name__,
                                "message": str(error),
                            }
                        ).decode("utf-8"),
                        _now_ms(),
                        commit_id,
                    ),
                )
                conn.commit()
        except sqlite3.Error:
            pass

    def _insert_or_verify_phase(
        self,
        conn: sqlite3.Connection,
        *,
        commit_id: str,
        phase: str,
        detail: Mapping[str, Any],
        now: int,
    ) -> None:
        detail_json = canonical_json_bytes(detail).decode("utf-8")
        existing = conn.execute(
            """
            SELECT detail_json
              FROM evidence_commit_phases
             WHERE commit_id=? AND phase=?
            """,
            (commit_id, phase),
        ).fetchone()
        if existing is not None:
            if str(existing["detail_json"]) != detail_json:
                raise EvidenceCommitConflict(
                    f"evidence commit phase {phase!r} changed during replay"
                )
            return
        conn.execute(
            """
            INSERT INTO evidence_commit_phases(
              commit_id, phase, detail_json, recorded_at_ms
            ) VALUES(?, ?, ?, ?)
            """,
            (commit_id, phase, detail_json, now),
        )

    def _phase_history(self, commit_id: str) -> tuple[str, ...]:
        with self._outbox_connection() as conn:
            rows = conn.execute(
                """
                SELECT phase
                  FROM evidence_commit_phases
                 WHERE commit_id=?
                 ORDER BY phase_sequence
                """,
                (commit_id,),
            ).fetchall()
        return tuple(str(row["phase"]) for row in rows)

    def _load_phase_detail(
        self,
        commit_id: str,
        phase: str,
    ) -> dict[str, Any] | None:
        with self._outbox_connection() as conn:
            row = conn.execute(
                """
                SELECT detail_json
                  FROM evidence_commit_phases
                 WHERE commit_id=? AND phase=?
                """,
                (commit_id, phase),
            ).fetchone()
        if row is None:
            return None
        return _decode_json_object(
            row["detail_json"],
            field=f"evidence_commit_phases[{phase!r}].detail_json",
        )

    def _load_commit_row(self, commit_id: str) -> sqlite3.Row:
        with self._outbox_connection() as conn:
            row = conn.execute(
                "SELECT * FROM evidence_commits WHERE commit_id=?",
                (commit_id,),
            ).fetchone()
        if row is None:
            raise EvidenceCommitIntegrityError(
                f"unknown evidence commit: {commit_id}"
            )
        return row

    @contextmanager
    def _outbox_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.outbox_path, timeout=30.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn
        finally:
            conn.close()

    def _observe_phase(self, phase: str) -> None:
        if self.phase_observer is not None:
            self.phase_observer(phase)


def _export_sqlite_database(
    path: Path,
    *,
    logical_name: str,
    snapshot_metadata: Mapping[str, Any] | None = None,
) -> bytes:
    """Export one consistent SQLite read transaction as canonical JSON."""
    with _sqlite_snapshot_connection(
        path,
        logical_name=logical_name,
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        schema_rows = conn.execute(
            """
            SELECT type, name, tbl_name, sql
              FROM sqlite_master
             WHERE name NOT LIKE 'sqlite_autoindex_%'
             ORDER BY type, name
            """
        ).fetchall()
        table_names = [
            str(row["name"])
            for row in schema_rows
            if row["type"] == "table"
            and not str(row["name"]).startswith("sqlite_")
        ]
        if conn.execute(
            """
            SELECT 1 FROM sqlite_master
             WHERE type='table' AND name='sqlite_sequence'
            """
        ).fetchone():
            table_names.append("sqlite_sequence")
        tables: list[dict[str, Any]] = []
        for table_name in sorted(set(table_names)):
            quoted = _quote_sqlite_identifier(table_name)
            column_rows = conn.execute(
                f"PRAGMA table_info({quoted})"
            ).fetchall()
            columns = [str(row["name"]) for row in column_rows]
            primary_keys = [
                (int(row["pk"]), str(row["name"]))
                for row in column_rows
                if int(row["pk"]) > 0
            ]
            order_columns = [
                name for _, name in sorted(primary_keys)
            ] or columns
            order_clause = ", ".join(
                _quote_sqlite_identifier(column)
                for column in order_columns
            )
            rows = conn.execute(
                f"SELECT * FROM {quoted}"
                + (f" ORDER BY {order_clause}" if order_clause else "")
            ).fetchall()
            tables.append(
                {
                    "name": table_name,
                    "columns": columns,
                    "rows": [
                        [_json_sqlite_value(row[column]) for column in columns]
                        for row in rows
                    ],
                }
            )
        document: dict[str, Any] = {
            "schema_version": SQLITE_EXPORT_SCHEMA_VERSION,
            "logical_name": logical_name,
            "schema": [
                {
                    "type": row["type"],
                    "name": row["name"],
                    "table": row["tbl_name"],
                    "sql": row["sql"],
                }
                for row in schema_rows
            ],
            "tables": tables,
        }
        if snapshot_metadata:
            document["snapshot_metadata"] = dict(snapshot_metadata)
        conn.rollback()
        return canonical_json_bytes(document)


_SQLITE_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_SQLITE_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_SQLITE_READ_FLAGS = os.O_RDONLY | _SQLITE_NOFOLLOW | _SQLITE_CLOEXEC
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
_SQLITE_SNAPSHOT_BASENAME = "snapshot.db"


@contextmanager
def _sqlite_snapshot_connection(
    path: Path,
    *,
    logical_name: str,
) -> Iterator[sqlite3.Connection]:
    """Open SQLite through inode-pinned aliases, never through the source path."""
    if not _SQLITE_NOFOLLOW:
        raise EvidenceCommitIntegrityError(
            "secure no-follow SQLite snapshot operations are unavailable"
        )
    source = _absolute_no_follow_path(path)
    if source.parent == source or not source.name:
        raise EvidenceCommitIntegrityError(
            f"SQLite evidence store path is invalid: {logical_name}"
        )

    with _directory_tree_fd(
        source.parent,
        create=False,
        error_type=EvidenceCommitIntegrityError,
        label=f"SQLite evidence store parent {logical_name}",
    ) as parent_fd:
        pinned: dict[str, int] = {}
        alias_dir_name: str | None = None
        alias_dir_fd: int | None = None
        try:
            pinned[""] = _open_sqlite_regular_file_at(
                parent_fd,
                source.name,
                logical_name=logical_name,
                missing_ok=False,
            )
            for suffix in _SQLITE_SIDECAR_SUFFIXES:
                descriptor = _open_sqlite_regular_file_at(
                    parent_fd,
                    source.name + suffix,
                    logical_name=logical_name,
                    missing_ok=True,
                )
                if descriptor is not None:
                    pinned[suffix] = descriptor

            alias_dir_name, alias_dir_fd = _create_sqlite_alias_directory(
                parent_fd,
                logical_name=logical_name,
            )
            for suffix, descriptor in pinned.items():
                _link_pinned_sqlite_file(
                    parent_fd=parent_fd,
                    source_name=source.name + suffix,
                    source_fd=descriptor,
                    alias_dir_fd=alias_dir_fd,
                    alias_name=_SQLITE_SNAPSHOT_BASENAME + suffix,
                    logical_name=logical_name,
                )

            _assert_sqlite_source_unchanged(
                parent_fd,
                source.name,
                pinned,
                logical_name=logical_name,
            )
            alias_directory = _path_for_open_directory(
                alias_dir_fd,
                fallback=source.parent / alias_dir_name,
                logical_name=logical_name,
            )
            alias_path = alias_directory / _SQLITE_SNAPSHOT_BASENAME
            _assert_path_matches_descriptor(
                alias_path,
                pinned[""],
                logical_name=logical_name,
            )
            connection = sqlite3.connect(
                f"{alias_path.as_uri()}?mode=ro",
                uri=True,
                timeout=30.0,
            )
            completed = False
            try:
                _assert_path_matches_descriptor(
                    alias_path,
                    pinned[""],
                    logical_name=logical_name,
                )
                _assert_sqlite_source_unchanged(
                    parent_fd,
                    source.name,
                    pinned,
                    logical_name=logical_name,
                )
                yield connection
                completed = True
            finally:
                try:
                    if completed:
                        _assert_path_matches_descriptor(
                            alias_path,
                            pinned[""],
                            logical_name=logical_name,
                        )
                        _assert_sqlite_source_unchanged(
                            parent_fd,
                            source.name,
                            pinned,
                            logical_name=logical_name,
                        )
                finally:
                    connection.close()
        finally:
            try:
                try:
                    if alias_dir_fd is not None:
                        _clear_sqlite_alias_directory(
                            alias_dir_fd,
                            logical_name=logical_name,
                        )
                finally:
                    if alias_dir_fd is not None:
                        os.close(alias_dir_fd)
            finally:
                try:
                    if alias_dir_name is not None:
                        try:
                            os.rmdir(alias_dir_name, dir_fd=parent_fd)
                        except FileNotFoundError:
                            pass
                        except OSError as exc:
                            raise EvidenceCommitIntegrityError(
                                "SQLite snapshot alias directory could not be "
                                f"removed safely for {logical_name}: {exc}"
                            ) from exc
                finally:
                    for descriptor in pinned.values():
                        os.close(descriptor)


def _open_sqlite_regular_file_at(
    parent_fd: int,
    name: str,
    *,
    logical_name: str,
    missing_ok: bool,
) -> int | None:
    try:
        descriptor = os.open(name, _SQLITE_READ_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if missing_ok and exc.errno == errno.ENOENT:
            return None
        if exc.errno == errno.ELOOP:
            raise EvidenceCommitIntegrityError(
                f"SQLite evidence store {logical_name} contains a symlink"
            ) from exc
        if exc.errno == errno.ENOENT:
            raise EvidenceCommitIntegrityError(
                f"SQLite evidence store does not exist: {logical_name}"
            ) from exc
        raise EvidenceCommitIntegrityError(
            "SQLite evidence store could not be opened without following "
            f"links for {logical_name}: {exc}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceCommitIntegrityError(
                f"SQLite evidence store is not a regular file: {logical_name}"
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _create_sqlite_alias_directory(
    parent_fd: int,
    *,
    logical_name: str,
) -> tuple[str, int]:
    for _attempt in range(16):
        name = (
            f".evidence-snapshot-{os.getpid()}-"
            f"{secrets.token_hex(16)}"
        )
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise EvidenceCommitIntegrityError(
                "SQLite snapshot alias directory could not be created for "
                f"{logical_name}: {exc}"
            ) from exc
        descriptor: int | None = None
        try:
            descriptor = _open_child_directory(
                parent_fd,
                name,
                create=False,
                error_type=EvidenceCommitIntegrityError,
                label=f"SQLite snapshot alias directory {logical_name}",
            )
            assert descriptor is not None
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise EvidenceCommitIntegrityError(
                    "SQLite snapshot alias directory is not private for "
                    f"{logical_name}"
                )
            return name, descriptor
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
    raise EvidenceCommitIntegrityError(
        f"SQLite snapshot alias name allocation failed for {logical_name}"
    )


def _link_pinned_sqlite_file(
    *,
    parent_fd: int,
    source_name: str,
    source_fd: int,
    alias_dir_fd: int,
    alias_name: str,
    logical_name: str,
) -> None:
    try:
        os.link(
            source_name,
            alias_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=alias_dir_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise EvidenceCommitIntegrityError(
            "SQLite evidence store changed during snapshot acquisition: "
            f"{logical_name}"
        ) from exc
    try:
        alias_metadata = os.stat(
            alias_name,
            dir_fd=alias_dir_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise EvidenceCommitIntegrityError(
            "SQLite snapshot alias could not be verified for "
            f"{logical_name}: {exc}"
        ) from exc
    if (
        not stat.S_ISREG(alias_metadata.st_mode)
        or not _same_file_identity(alias_metadata, os.fstat(source_fd))
    ):
        raise EvidenceCommitIntegrityError(
            "SQLite evidence store changed during snapshot acquisition: "
            f"{logical_name}"
        )


def _assert_sqlite_source_unchanged(
    parent_fd: int,
    basename: str,
    pinned: Mapping[str, int],
    *,
    logical_name: str,
) -> None:
    for suffix in ("", *_SQLITE_SIDECAR_SUFFIXES):
        expected = pinned.get(suffix)
        try:
            observed = os.stat(
                basename + suffix,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if expected is None:
                continue
            raise EvidenceCommitIntegrityError(
                "SQLite evidence store changed during snapshot acquisition: "
                f"{logical_name}"
            )
        except OSError as exc:
            raise EvidenceCommitIntegrityError(
                "SQLite evidence store could not be revalidated for "
                f"{logical_name}: {exc}"
            ) from exc
        if (
            expected is None
            or not stat.S_ISREG(observed.st_mode)
            or not _same_file_identity(observed, os.fstat(expected))
        ):
            raise EvidenceCommitIntegrityError(
                "SQLite evidence store changed during snapshot acquisition: "
                f"{logical_name}"
            )


def _path_for_open_directory(
    descriptor: int,
    *,
    fallback: Path,
    logical_name: str,
) -> Path:
    candidates: list[Path] = []
    if fcntl is not None and hasattr(fcntl, "F_GETPATH"):
        try:
            raw = fcntl.fcntl(
                descriptor,
                fcntl.F_GETPATH,
                b"\0" * 4096,
            )
            encoded = bytes(raw).split(b"\0", 1)[0]
            if encoded:
                candidates.append(Path(os.fsdecode(encoded)))
        except (OSError, TypeError, ValueError):
            pass
    proc_descriptor = Path(f"/proc/self/fd/{descriptor}")
    try:
        candidates.append(Path(os.readlink(proc_descriptor)))
    except OSError:
        pass
    candidates.append(fallback)

    expected = os.fstat(descriptor)
    for candidate in candidates:
        if not candidate.is_absolute():
            continue
        try:
            observed = os.stat(candidate, follow_symlinks=False)
        except OSError:
            continue
        if (
            stat.S_ISDIR(observed.st_mode)
            and _same_file_identity(observed, expected)
        ):
            return candidate
    raise EvidenceCommitIntegrityError(
        "SQLite snapshot alias directory path changed during acquisition: "
        f"{logical_name}"
    )


def _assert_path_matches_descriptor(
    path: Path,
    descriptor: int,
    *,
    logical_name: str,
) -> None:
    try:
        observed = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise EvidenceCommitIntegrityError(
            "SQLite snapshot alias path changed during acquisition: "
            f"{logical_name}"
        ) from exc
    if (
        not stat.S_ISREG(observed.st_mode)
        or not _same_file_identity(observed, os.fstat(descriptor))
    ):
        raise EvidenceCommitIntegrityError(
            "SQLite snapshot alias path changed during acquisition: "
            f"{logical_name}"
        )


def _clear_sqlite_alias_directory(
    alias_dir_fd: int,
    *,
    logical_name: str,
) -> None:
    try:
        names = os.listdir(alias_dir_fd)
    except OSError as exc:
        raise EvidenceCommitIntegrityError(
            "SQLite snapshot alias directory could not be listed for "
            f"{logical_name}: {exc}"
        ) from exc
    for name in names:
        try:
            metadata = os.stat(
                name,
                dir_fd=alias_dir_fd,
                follow_symlinks=False,
            )
            if stat.S_ISDIR(metadata.st_mode):
                raise EvidenceCommitIntegrityError(
                    "SQLite snapshot alias directory contains an unexpected "
                    f"directory for {logical_name}"
                )
            os.unlink(name, dir_fd=alias_dir_fd)
        except EvidenceCommitIntegrityError:
            raise
        except OSError as exc:
            raise EvidenceCommitIntegrityError(
                "SQLite snapshot alias could not be removed safely for "
                f"{logical_name}: {exc}"
            ) from exc


def _same_file_identity(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _json_sqlite_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "value": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, float) and not math.isfinite(value):
        raise EvidenceCommitIntegrityError(
            "SQLite evidence export contains a non-finite float"
        )
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise EvidenceCommitIntegrityError(
        f"SQLite evidence export contains unsupported value: {type(value)!r}"
    )


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


_RESERVED_EVIDENCE_ROOT_NAMES = frozenset(
    {"cas", "checkpoints", "evidence-commits.db"}
    | {
        f"evidence-commits.db{suffix}"
        for suffix in _SQLITE_SIDECAR_SUFFIXES
    }
)


def _safe_relative_path(value: str) -> PurePosixPath:
    raw = str(value).strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0].casefold() in _RESERVED_EVIDENCE_ROOT_NAMES
    ):
        raise ValueError(f"unsafe evidence artifact path: {value!r}")
    return path


def _write_evidence_file(
    root: Path,
    relative_path: str,
    content: bytes,
) -> None:
    relative = _safe_relative_path(relative_path)
    value = bytes(content)
    with _evidence_parent_fd(
        root,
        relative,
        create_parents=True,
    ) as parent_fd:
        created, existing = _append_only_file_at(
            parent_fd,
            relative.name,
            value,
            error_type=EvidenceCommitIntegrityError,
            label=f"evidence file {relative.as_posix()}",
        )
    if not created and existing != value:
        raise EvidenceCommitConflict(
            "immutable evidence file already differs: "
            f"{relative.as_posix()}"
        )


def _read_evidence_file(root: Path, relative_path: str) -> bytes:
    relative = _safe_relative_path(relative_path)
    with _evidence_parent_fd(
        root,
        relative,
        create_parents=False,
    ) as parent_fd:
        # A process death after link publication can leave the temporary
        # sibling as a second hard link; immutable byte equality remains the
        # recovery invariant.
        return _read_regular_file_at(
            parent_fd,
            relative.name,
            error_type=EvidenceCommitIntegrityError,
            label=f"evidence file {relative.as_posix()}",
            require_single_link=False,
        )


@contextmanager
def _evidence_parent_fd(
    root: Path,
    relative_path: PurePosixPath,
    *,
    create_parents: bool,
) -> Iterator[int]:
    descriptors: list[int] = []
    with _directory_tree_fd(
        root,
        create=False,
        error_type=EvidenceCommitIntegrityError,
        label="evidence root",
    ) as root_fd:
        parent_fd = root_fd
        try:
            for index, part in enumerate(relative_path.parts[:-1]):
                child_fd = _open_child_directory(
                    parent_fd,
                    part,
                    create=create_parents,
                    error_type=EvidenceCommitIntegrityError,
                    label=(
                        "evidence directory "
                        + "/".join(relative_path.parts[: index + 1])
                    ),
                )
                assert child_fd is not None
                descriptors.append(child_fd)
                parent_fd = child_fd
            yield parent_fd
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def _decode_json_object(value: Any, *, field: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise EvidenceCommitIntegrityError(
            f"outbox {field} is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise EvidenceCommitIntegrityError(
            f"outbox {field} must be a JSON object"
        )
    return payload


def _set_projection_once(
    projection: dict[str, Any],
    key: str,
    value: Any,
) -> None:
    if projection.get(key) is not None:
        raise EvidenceCommitIntegrityError(
            f"tracer projection observed duplicate {key}"
        )
    projection[key] = value


def _insert_projection_record(
    target: dict[str, Any],
    key: str,
    value: Mapping[str, Any],
) -> None:
    if not key or key in target:
        raise EvidenceCommitIntegrityError(
            f"tracer projection observed duplicate or empty key: {key!r}"
        )
    target[key] = dict(value)


def _now_ms() -> int:
    return int(time.time_ns() // 1_000_000)


def _begin_outbox_transaction(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        raise EvidenceCommitError(
            f"evidence commit outbox is locked by another committer: {exc}"
        ) from exc


__all__ = [
    "EVIDENCE_COMMIT_EVENT_KIND",
    "EVIDENCE_COMMIT_SCHEMA_VERSION",
    "EvidenceArtifact",
    "EvidenceCommitConflict",
    "EvidenceCommitError",
    "EvidenceCommitIntegrityError",
    "EvidenceCommitRequest",
    "EvidenceCommitResult",
    "EvidenceCommitter",
    "EvidenceGradeHistory",
    "HmacCheckpointAuthority",
    "SQLITE_EXPORT_SCHEMA_VERSION",
    "TRACER_PROJECTION_SCHEMA_VERSION",
    "initial_tracer_evidence_projection",
    "reduce_tracer_evidence_projection",
]
