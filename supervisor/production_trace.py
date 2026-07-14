"""Persist one already ledger-verified Harness v1 process-event projection.

This module deliberately records only L1 process evidence.  It does not turn a
successful supervisor gate into hidden-verifier, causal, portable, ROI, or
auto-improvement evidence.  The production boundary must resolve and verify the
source event before constructing ``ProductionTraceEvidence``; this storage
primitive does not independently own the event ledger.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

try:  # pragma: no cover - exercised on POSIX CI and development hosts.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback is thread-only.
    fcntl = None  # type: ignore[assignment]

from .grade_revisions import (
    DecisionGradeCitation,
    GradeBook,
    GradeBookError,
    GradeTerminalCommit,
    RunEnvelopeRef,
    SupersessionConflict,
)
from .task_environment import Grade
from .trace_graph import (
    TRACE_CLOSURE_BINDING_ATTRIBUTE,
    EdgeType,
    NodeType,
    TraceClosureBinding,
    TraceEdge,
    TraceGraph,
    TraceGraphStore,
    TraceIdentity,
    TraceNode,
    TracePlanningArtifactRef,
    canonical_revision_hash,
    trace_instance_id_from_hash,
)


PRODUCTION_TRACE_SCHEMA_VERSION = "supervisor-production-trace/v1"
PRODUCTION_TRACE_RECEIPT_SCHEMA_VERSION = (
    "supervisor-production-trace-receipt/v1"
)
PRODUCTION_TRACE_NAMESPACE = "harness-v1/production-trace"
PRODUCTION_TRACE_VERIFIER_ID = "supervisor.production-trace.process-verifier"
PRODUCTION_TRACE_VERIFIER_VERSION = "1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID7_TIMESTAMP_MAX = (1 << 48) - 1
_PRODUCTION_TRACE_THREAD_LOCK = threading.RLock()


class ProductionTraceError(RuntimeError):
    """Raised when post-execution process evidence cannot be proven."""


@dataclass(frozen=True)
class ProductionTraceEvidence:
    """Canonical projection of one already ledger-verified gate event."""

    task_id: str
    task_hash: str
    run_id: str
    run_envelope_hash: str
    frozen_result_hash: str
    gate: str
    gate_hash: str
    planning_artifacts: tuple[TracePlanningArtifactRef, ...]
    runtime_provenance: Mapping[str, Any]
    result_provenance: Mapping[str, Any]
    source_event_id: str
    source_event_hash: str
    source_event_state: str
    source_event_recorded_at_ms: int
    final_gate_result: Mapping[str, Any]
    schema_version: str = PRODUCTION_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PRODUCTION_TRACE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported production trace evidence schema: "
                f"{self.schema_version}"
            )
        for field_name in (
            "task_id",
            "run_id",
            "gate",
            "source_event_id",
        ):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"{field_name} must be non-empty")
            object.__setattr__(self, field_name, value)
        for field_name in (
            "task_hash",
            "run_envelope_hash",
            "frozen_result_hash",
            "gate_hash",
            "source_event_hash",
        ):
            value = str(getattr(self, field_name)).strip().lower()
            if not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{field_name} must be a canonical sha256")
            object.__setattr__(self, field_name, value)
        state = str(self.source_event_state).strip()
        if state not in {"completed", "failed"}:
            raise ValueError(
                "source_event_state must be completed or failed"
            )
        object.__setattr__(self, "source_event_state", state)
        if (
            isinstance(self.source_event_recorded_at_ms, bool)
            or not 0 <= int(self.source_event_recorded_at_ms)
            <= _UUID7_TIMESTAMP_MAX - 10
        ):
            raise ValueError(
                "source_event_recorded_at_ms must leave room for ten UUIDv7 "
                "trace identities"
            )
        object.__setattr__(
            self,
            "source_event_recorded_at_ms",
            int(self.source_event_recorded_at_ms),
        )
        planning_artifacts = tuple(
            artifact
            if isinstance(artifact, TracePlanningArtifactRef)
            else TracePlanningArtifactRef.from_mapping(artifact)
            for artifact in self.planning_artifacts
        )
        if not planning_artifacts:
            raise ValueError(
                "planning_artifacts must contain at least one hash pin"
            )
        if len({
            (artifact.kind, artifact.path)
            for artifact in planning_artifacts
        }) != len(planning_artifacts):
            raise ValueError(
                "planning_artifacts must be unique by kind and path"
            )
        object.__setattr__(
            self,
            "planning_artifacts",
            tuple(sorted(
                planning_artifacts,
                key=lambda artifact: (
                    artifact.kind,
                    artifact.path,
                    artifact.sha256,
                ),
            )),
        )
        object.__setattr__(
            self,
            "runtime_provenance",
            _freeze_mapping(
                self.runtime_provenance,
                field="runtime_provenance",
            ),
        )
        object.__setattr__(
            self,
            "result_provenance",
            _freeze_mapping(
                self.result_provenance,
                field="result_provenance",
            ),
        )
        object.__setattr__(
            self,
            "final_gate_result",
            _freeze_mapping(
                self.final_gate_result,
                field="final_gate_result",
            ),
        )
        if (
            self.runtime_provenance.get("run_envelope_hash")
            != self.run_envelope_hash
        ):
            raise ValueError(
                "runtime_provenance must pin the exact run_envelope_hash"
            )
        if (
            self.result_provenance.get("frozen_result_hash")
            != self.frozen_result_hash
        ):
            raise ValueError(
                "result_provenance must pin the exact frozen_result_hash"
            )
        for field_name in ("assignment_id", "arm"):
            if not str(
                self.runtime_provenance.get(field_name) or ""
            ).strip():
                raise ValueError(
                    f"runtime_provenance.{field_name} must be non-empty"
                )
        if not self.gate_status:
            raise ValueError(
                "final_gate_result.status must be non-empty"
            )

    @property
    def gate_status(self) -> str:
        return str(self.final_gate_result.get("status") or "").strip().lower()

    @property
    def passed(self) -> bool:
        """Whether the persisted process event completed, not gate outcome."""
        return self.source_event_state == "completed"

    @property
    def assignment_id(self) -> str:
        return str(self.runtime_provenance["assignment_id"])

    @property
    def arm(self) -> str:
        return str(self.runtime_provenance["arm"])

    @property
    def fingerprint(self) -> str:
        return canonical_revision_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_hash": self.task_hash,
            "run_id": self.run_id,
            "run_envelope_hash": self.run_envelope_hash,
            "frozen_result_hash": self.frozen_result_hash,
            "gate": self.gate,
            "gate_hash": self.gate_hash,
            "planning_artifacts": [
                artifact.to_dict()
                for artifact in self.planning_artifacts
            ],
            "runtime_provenance": _thaw(self.runtime_provenance),
            "result_provenance": _thaw(self.result_provenance),
            "source_event_id": self.source_event_id,
            "source_event_hash": self.source_event_hash,
            "source_event_state": self.source_event_state,
            "source_event_recorded_at_ms": (
                self.source_event_recorded_at_ms
            ),
            "final_gate_result": _thaw(self.final_gate_result),
        }


@dataclass(frozen=True)
class ProductionTraceReceipt:
    """Hash-pinned receipt for a persisted and revalidated L1 trace."""

    claim_cap: str
    closure: Mapping[str, Any]
    trace_store_path: str
    gradebook_path: str
    record_fingerprint: str
    trace_store_sha256: str
    gradebook_sha256: str
    trace_graph_hash: str
    source_event_id: str
    source_event_hash: str
    promotion: TraceIdentity
    grade_citation: DecisionGradeCitation
    grade_terminal_commit_hash: str
    verifier_config_hash: str
    verifier_implementation_hash: str
    evidence: Mapping[str, Any]
    trace_graph: Mapping[str, Any]
    grade_revision: Mapping[str, Any]
    grade_terminal_commit: Mapping[str, Any]
    grade_decision: Mapping[str, Any]
    schema_version: str = PRODUCTION_TRACE_RECEIPT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claim_cap": self.claim_cap,
            "closure": _thaw(self.closure),
            "trace_store_path": self.trace_store_path,
            "gradebook_path": self.gradebook_path,
            "record_fingerprint": self.record_fingerprint,
            "trace_store_sha256": self.trace_store_sha256,
            "gradebook_sha256": self.gradebook_sha256,
            "trace_graph_hash": self.trace_graph_hash,
            "source_event_id": self.source_event_id,
            "source_event_hash": self.source_event_hash,
            "promotion": self.promotion.to_dict(),
            "grade_citation": self.grade_citation.to_dict(),
            "grade_terminal_commit_hash": (
                self.grade_terminal_commit_hash
            ),
            "verifier_config_hash": self.verifier_config_hash,
            "verifier_implementation_hash": (
                self.verifier_implementation_hash
            ),
            "evidence": _thaw(self.evidence),
            "trace_graph": _thaw(self.trace_graph),
            "grade_revision": _thaw(self.grade_revision),
            "grade_terminal_commit": _thaw(
                self.grade_terminal_commit
            ),
            "grade_decision": _thaw(self.grade_decision),
        }


class ProductionTraceRecorder:
    """Persist one verified projection and re-check its dedicated stores."""

    def __init__(
        self,
        *,
        trace_store_path: str | Path,
        gradebook_path: str | Path,
    ) -> None:
        self.trace_store_path = _storage_path(trace_store_path)
        self.gradebook_path = _storage_path(gradebook_path)
        if self.trace_store_path == self.gradebook_path:
            raise ValueError(
                "trace_store_path and gradebook_path must be separate"
            )

    def record(
        self,
        evidence: ProductionTraceEvidence,
    ) -> ProductionTraceReceipt:
        with _production_trace_recording_lock(
            Path(self.trace_store_path),
            Path(self.gradebook_path),
        ):
            return self._record_locked(evidence)

    def _record_locked(
        self,
        evidence: ProductionTraceEvidence,
    ) -> ProductionTraceReceipt:
        if not isinstance(evidence, ProductionTraceEvidence):
            raise TypeError(
                "ProductionTraceRecorder.record requires "
                "ProductionTraceEvidence"
            )
        self._preflight_existing_trace(evidence)
        run = RunEnvelopeRef(
            run_id=evidence.run_id,
            run_envelope_hash=evidence.run_envelope_hash,
            frozen_result_hash=evidence.frozen_result_hash,
        )
        verifier_hash = _implementation_hash()
        verifier_config_hash = canonical_revision_hash({
            "schema_version": (
                "supervisor-production-trace-verifier-config/v1"
            ),
            "claim_cap": "L1",
            "hidden_outcome_evidence": False,
            "gate": evidence.gate,
            "task_hash": evidence.task_hash,
        })

        try:
            with GradeBook(self.gradebook_path) as gradebook:
                revision = gradebook.append_grade(
                    run=run,
                    grade=Grade(
                        verifier_id=PRODUCTION_TRACE_VERIFIER_ID,
                        verifier_version=PRODUCTION_TRACE_VERIFIER_VERSION,
                        verifier_hash=verifier_hash,
                        frozen_result_hash=evidence.frozen_result_hash,
                        passed=evidence.passed,
                        score=1.0 if evidence.passed else 0.0,
                        evidence={
                            "claim_cap": "L1",
                            "hidden_outcome_evidence": False,
                            "record_fingerprint": evidence.fingerprint,
                            "production_trace_evidence": (
                                evidence.to_dict()
                            ),
                            "source_event_id": evidence.source_event_id,
                            "source_event_hash": evidence.source_event_hash,
                            "source_event_state": (
                                evidence.source_event_state
                            ),
                            "gate": evidence.gate,
                            "gate_hash": evidence.gate_hash,
                            "final_gate_result": _thaw(
                                evidence.final_gate_result
                            ),
                        },
                        failure_classification=(
                            ""
                            if evidence.passed
                            else "source_event_failed"
                        ),
                    ),
                    verifier_config_hash=verifier_config_hash,
                    verifier_implementation_hash=verifier_hash,
                )
                terminal_commit = gradebook.commit_terminal_grade(
                    grade_id=revision.grade_id,
                    revision_hash=revision.revision_hash,
                    experiment_id=(
                        "production-trace:"
                        + canonical_revision_hash({
                            "task_id": evidence.task_id,
                            "run_id": evidence.run_id,
                            "gate": evidence.gate,
                        })
                    ),
                    task_id=evidence.task_id,
                    arm=evidence.arm,
                    terminal_state=evidence.source_event_state,
                    terminal_state_hash=evidence.source_event_hash,
                )
                citation = DecisionGradeCitation(
                    grade_id=revision.grade_id,
                    revision_hash=revision.revision_hash,
                )
                persisted_commit = gradebook.get_terminal_commit(
                    revision.grade_id
                )
                if (
                    persisted_commit is None
                    or persisted_commit != terminal_commit
                    or persisted_commit.terminal_state_hash
                    != evidence.source_event_hash
                ):
                    raise ProductionTraceError(
                        "process grade terminal commit is missing or "
                        "does not pin the persisted source event"
                    )
                validation = gradebook.validate_decision((citation,))
                if not validation.accepted:
                    raise ProductionTraceError(
                        "process grade citation lacks terminal authority: "
                        + _canonical_json(validation.to_dict())
                    )
                decision_id = (
                    "production-trace:"
                    + canonical_revision_hash({
                        "run_id": evidence.run_id,
                        "gate": evidence.gate,
                        "source_event_hash": evidence.source_event_hash,
                    })
                )
                decision = gradebook.record_decision(
                    decision_id=decision_id,
                    decision={
                        "claim_cap": "L1",
                        "hidden_outcome_evidence": False,
                        "record_fingerprint": evidence.fingerprint,
                        "task_id": evidence.task_id,
                        "task_hash": evidence.task_hash,
                        "run_id": evidence.run_id,
                        "run_envelope_hash": (
                            evidence.run_envelope_hash
                        ),
                        "frozen_result_hash": (
                            evidence.frozen_result_hash
                        ),
                        "gate": evidence.gate,
                        "gate_hash": evidence.gate_hash,
                        "planning_artifacts": [
                            artifact.to_dict()
                            for artifact in evidence.planning_artifacts
                        ],
                        "source_event_hash": evidence.source_event_hash,
                        "final_gate_result": _thaw(
                            evidence.final_gate_result
                        ),
                    },
                    citations=(citation,),
                )
                graph, promotion = _build_graph(
                    evidence=evidence,
                    revision=revision,
                    terminal_commit=terminal_commit,
                    citation=citation,
                    decision_hash=decision.decision_hash,
                )
                persisted = self._persist_exact_graph(graph)
                binding = TraceClosureBinding(
                    task_id=evidence.task_id,
                    run_id=evidence.run_id,
                    gate=evidence.gate,
                    planning_artifacts=evidence.planning_artifacts,
                )
                closure = persisted.validate_closure(
                    now=datetime.fromtimestamp(
                        evidence.source_event_recorded_at_ms / 1000,
                        tz=timezone.utc,
                    ),
                    expected_binding=binding,
                    decision_grade_validator=gradebook,
                )
                if not closure.ok:
                    raise ProductionTraceError(
                        "persisted production trace does not close: "
                        + _canonical_json(closure.to_dict())
                    )
                trace = persisted.promotion_trace(promotion)
                expected_path = (
                    NodeType.OBJ,
                    NodeType.REQ,
                    NodeType.TEST,
                    NodeType.ASN,
                    NodeType.RUN,
                    NodeType.ART,
                    NodeType.GRADE,
                    NodeType.ANL,
                    NodeType.DEC,
                    NodeType.PROMOTION,
                )
                observed_path = tuple(
                    node.identity.node_type for node in trace
                )
                if observed_path != expected_path:
                    raise ProductionTraceError(
                        "persisted production trace has a non-canonical "
                        "objective-to-promotion path"
                    )
        except SupersessionConflict as exc:
            raise ProductionTraceError(
                "immutable production trace evidence changed"
            ) from exc
        except GradeBookError as exc:
            raise ProductionTraceError(
                f"production trace grade authority failed closed: {exc}"
            ) from exc

        trace_store_sha256 = _file_sha256(self.trace_store_path)
        gradebook_sha256 = _file_sha256(self.gradebook_path)
        return ProductionTraceReceipt(
            claim_cap="L1",
            closure=MappingProxyType(closure.to_dict()),
            trace_store_path=self.trace_store_path,
            gradebook_path=self.gradebook_path,
            record_fingerprint=evidence.fingerprint,
            trace_store_sha256=trace_store_sha256,
            gradebook_sha256=gradebook_sha256,
            trace_graph_hash=sha256(
                persisted.canonical_bytes()
            ).hexdigest(),
            source_event_id=evidence.source_event_id,
            source_event_hash=evidence.source_event_hash,
            promotion=promotion,
            grade_citation=citation,
            grade_terminal_commit_hash=terminal_commit.commit_hash,
            verifier_config_hash=verifier_config_hash,
            verifier_implementation_hash=verifier_hash,
            evidence=MappingProxyType(evidence.to_dict()),
            trace_graph=MappingProxyType(persisted.to_dict()),
            grade_revision=MappingProxyType(revision.to_dict()),
            grade_terminal_commit=MappingProxyType(
                terminal_commit.to_dict()
            ),
            grade_decision=MappingProxyType(decision.to_dict()),
        )

    def _preflight_existing_trace(
        self,
        evidence: ProductionTraceEvidence,
    ) -> None:
        with TraceGraphStore(self.trace_store_path) as store:
            existing = store.load()
        if not existing.nodes:
            return
        promotions = tuple(
            node
            for node in existing.nodes
            if node.identity.node_type is NodeType.PROMOTION
        )
        if len(promotions) != 1:
            raise ProductionTraceError(
                "production trace store is not dedicated to one recording"
            )
        if (
            promotions[0].attributes.get("record_fingerprint")
            != evidence.fingerprint
        ):
            raise ProductionTraceError(
                "production trace retry changed immutable evidence"
            )

    def _persist_exact_graph(self, graph: TraceGraph) -> TraceGraph:
        with TraceGraphStore(self.trace_store_path) as store:
            existing = store.load()
            if (
                existing.nodes
                and existing.canonical_bytes() != graph.canonical_bytes()
            ):
                raise ProductionTraceError(
                    "production trace store is not dedicated to this exact "
                    "post-execution trace"
                )
            store.append(graph)
            persisted = store.load()
        if persisted.canonical_bytes() != graph.canonical_bytes():
            raise ProductionTraceError(
                "persisted production trace differs from the canonical graph"
            )
        return persisted


def _build_graph(
    *,
    evidence: ProductionTraceEvidence,
    revision: Any,
    terminal_commit: GradeTerminalCommit,
    citation: DecisionGradeCitation,
    decision_hash: str,
) -> tuple[TraceGraph, TraceIdentity]:
    binding = TraceClosureBinding(
        task_id=evidence.task_id,
        run_id=evidence.run_id,
        gate=evidence.gate,
        planning_artifacts=evidence.planning_artifacts,
    )
    payloads: dict[NodeType, Mapping[str, Any]] = {
        NodeType.OBJ: {
            "objective": "Keep Harness v1 claims bounded by persisted evidence",
            "task_id": evidence.task_id,
            "task_hash": evidence.task_hash,
        },
        NodeType.REQ: {
            "requirement": (
                "Every accepted process claim must close from objective "
                "through one persisted terminal grade"
            ),
            "task_id": evidence.task_id,
        },
        NodeType.TEST: {
            "gate": evidence.gate,
            "gate_hash": evidence.gate_hash,
            "planning_artifacts": [
                artifact.to_dict()
                for artifact in evidence.planning_artifacts
            ],
        },
        NodeType.ASN: {
            "runtime_provenance": _thaw(evidence.runtime_provenance),
        },
        NodeType.RUN: {
            "runtime_run_id": evidence.run_id,
            "run_envelope_hash": evidence.run_envelope_hash,
            "runtime_provenance": _thaw(evidence.runtime_provenance),
        },
        NodeType.ART: {
            "frozen_result_hash": evidence.frozen_result_hash,
            "result_provenance": _thaw(evidence.result_provenance),
            "source_event": {
                "event_id": evidence.source_event_id,
                "event_hash": evidence.source_event_hash,
                "state": evidence.source_event_state,
                "recorded_at_ms": evidence.source_event_recorded_at_ms,
            },
        },
        NodeType.GRADE: {
            "record_kind": "grade_revision",
            **revision.to_dict(),
            "terminal_commit": terminal_commit.to_dict(),
        },
        NodeType.ANL: {
            "claim_cap": "L1",
            "hidden_outcome_evidence": False,
            "final_gate_result": _thaw(evidence.final_gate_result),
        },
        NodeType.DEC: {
            TRACE_CLOSURE_BINDING_ATTRIBUTE: binding.to_dict(),
            "claim_cap": "L1",
            "decision_hash": decision_hash,
            "grade_citations": [citation.to_dict()],
        },
        NodeType.PROMOTION: {
            "claim_cap": "L1",
            "record_fingerprint": evidence.fingerprint,
            "gate_hash": evidence.gate_hash,
            "source_event_hash": evidence.source_event_hash,
        },
    }
    logical_ids = {
        node_type: f"{node_type.value}-PRODUCTION-TRACE"
        for node_type in payloads
    }
    revisions = {
        node_type: (
            revision.revision_hash
            if node_type is NodeType.GRADE
            else (
                evidence.run_envelope_hash
                if node_type is NodeType.RUN
                else (
                    evidence.frozen_result_hash
                    if node_type is NodeType.ART
                    else canonical_revision_hash(payload)
                )
            )
        )
        for node_type, payload in payloads.items()
    }
    identities = {
        node_type: TraceIdentity(
            namespace=PRODUCTION_TRACE_NAMESPACE,
            node_type=node_type,
            logical_id=logical_ids[node_type],
            revision_hash=revisions[node_type],
            instance_id=trace_instance_id_from_hash(
                timestamp_ms=evidence.source_event_recorded_at_ms + index,
                content_hash=revisions[node_type],
                domain=(
                    "supervisor-production-trace:"
                    f"{evidence.run_id}:{node_type.value}"
                ),
            ),
        )
        for index, node_type in enumerate(payloads, start=1)
    }
    nodes = {
        node_type: TraceNode(
            identity=identities[node_type],
            pinned=node_type is NodeType.RUN,
            runtime_evidence=node_type is NodeType.ART,
            verifier_id=(
                revision.verifier_id
                if node_type is NodeType.GRADE
                else None
            ),
            verifier_revision_hash=(
                revision.verifier_implementation_hash
                if node_type is NodeType.GRADE
                else None
            ),
            attributes=payload,
        )
        for node_type, payload in payloads.items()
    }
    edges = (
        TraceEdge(
            nodes[NodeType.REQ].identity,
            EdgeType.IMPLEMENTS,
            nodes[NodeType.OBJ].identity,
        ),
        TraceEdge(
            nodes[NodeType.TEST].identity,
            EdgeType.TESTS,
            nodes[NodeType.REQ].identity,
        ),
        TraceEdge(
            nodes[NodeType.ASN].identity,
            EdgeType.SUPPORTS,
            nodes[NodeType.TEST].identity,
        ),
        TraceEdge(
            nodes[NodeType.RUN].identity,
            EdgeType.ASSIGNED_BY,
            nodes[NodeType.ASN].identity,
        ),
        TraceEdge(
            nodes[NodeType.ART].identity,
            EdgeType.DERIVED_FROM,
            nodes[NodeType.RUN].identity,
        ),
        TraceEdge(
            nodes[NodeType.GRADE].identity,
            EdgeType.EVALUATES,
            nodes[NodeType.ART].identity,
        ),
        TraceEdge(
            nodes[NodeType.ANL].identity,
            EdgeType.DERIVED_FROM,
            nodes[NodeType.GRADE].identity,
        ),
        TraceEdge(
            nodes[NodeType.DEC].identity,
            EdgeType.DERIVED_FROM,
            nodes[NodeType.ANL].identity,
        ),
        TraceEdge(
            nodes[NodeType.PROMOTION].identity,
            EdgeType.PROMOTES,
            nodes[NodeType.DEC].identity,
        ),
    )
    return (
        TraceGraph(nodes=nodes.values(), edges=edges),
        nodes[NodeType.PROMOTION].identity,
    )


def _storage_path(path: str | Path) -> str:
    raw = str(path).strip()
    if not raw or raw == ":memory:":
        raise ValueError(
            "production trace stores require durable filesystem paths"
        )
    return str(Path(path).expanduser().resolve())


@contextlib.contextmanager
def _production_trace_recording_lock(
    trace_store_path: Path,
    gradebook_path: Path,
):
    parents = {
        trace_store_path.parent.resolve(),
        gradebook_path.parent.resolve(),
    }
    if len(parents) != 1:
        raise ProductionTraceError(
            "production trace stores must share one dedicated directory"
        )
    parent = next(iter(parents))
    parent.mkdir(parents=True, exist_ok=True)
    lock_path = parent / ".production-trace.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with _PRODUCTION_TRACE_THREAD_LOCK:
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise ProductionTraceError(
                "cannot open production trace recording lock"
            ) from exc
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
            os.fsync(descriptor)
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _implementation_hash() -> str:
    try:
        return sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError as exc:
        raise ProductionTraceError(
            "cannot hash the production trace verifier implementation"
        ) from exc


def _file_sha256(path: str) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _freeze_mapping(
    value: Mapping[str, Any],
    *,
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    normalized = json.loads(_canonical_json(value))
    if not isinstance(normalized, dict) or not normalized:
        raise ValueError(f"{field} must be a non-empty object")
    return MappingProxyType(normalized)


def _thaw(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            _json_compatible(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("production trace payload must be canonical JSON") from exc


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_compatible(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


__all__ = [
    "PRODUCTION_TRACE_RECEIPT_SCHEMA_VERSION",
    "PRODUCTION_TRACE_SCHEMA_VERSION",
    "ProductionTraceError",
    "ProductionTraceEvidence",
    "ProductionTraceReceipt",
    "ProductionTraceRecorder",
]
