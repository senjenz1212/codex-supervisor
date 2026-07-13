"""Typed, versioned trace identities and closure validation."""
from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID


TRACE_GRAPH_SCHEMA_VERSION = "supervisor-trace-graph/v1"
TRACE_GRAPH_STORE_SCHEMA_VERSION = "supervisor-trace-graph-store/v1"
TRACE_CLOSURE_BINDING_SCHEMA_VERSION = "supervisor-trace-closure-binding/v1"
TRACE_CLOSURE_BINDING_ATTRIBUTE = "trace_closure_binding"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_LOGICAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_LEGACY_BARE_ID_RE = re.compile(r"^P[0-9]+$", re.IGNORECASE)
_UUID7_RANDOM_MASK = (1 << 74) - 1
_UUID7_TIMESTAMP_MAX = (1 << 48) - 1
_UUID7_LOCK = threading.Lock()
_UUID7_LAST_TIMESTAMP_MS = -1
_UUID7_LAST_RANDOM = -1


class NodeType(str, Enum):
    """Harness-v1 trace node types."""

    OBJ = "OBJ"
    CLAIM = "CLAIM"
    REQ = "REQ"
    ADR = "ADR"
    ISSUE = "ISSUE"
    TEST = "TEST"
    TASK = "TASK"
    EXP = "EXP"
    ASN = "ASN"
    RUN = "RUN"
    ART = "ART"
    GRADE = "GRADE"
    ANL = "ANL"
    DEC = "DEC"
    POL = "POL"
    DEP = "DEP"
    PROMOTION = "PROMOTION"


class EdgeType(str, Enum):
    """Directed trace relations; source is the assertion-bearing record."""

    IMPLEMENTS = "implements"
    TESTS = "tests"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    DERIVED_FROM = "derived_from"
    ASSIGNED_BY = "assigned_by"
    EVALUATES = "evaluates"
    SUPERSEDES = "supersedes"
    INVALIDATES = "invalidates"
    PROMOTES = "promotes"
    ROLLS_BACK = "rolls_back"


_ALLOWED_EDGE_ENDPOINTS: Mapping[
    EdgeType,
    frozenset[tuple[NodeType, NodeType]],
] = {
    EdgeType.IMPLEMENTS: frozenset({
        (NodeType.REQ, NodeType.OBJ),
        (NodeType.ADR, NodeType.OBJ),
        (NodeType.ADR, NodeType.REQ),
        (NodeType.ISSUE, NodeType.REQ),
        (NodeType.TASK, NodeType.ISSUE),
        (NodeType.POL, NodeType.DEC),
        (NodeType.DEP, NodeType.DEC),
    }),
    EdgeType.TESTS: frozenset({
        (NodeType.TEST, NodeType.REQ),
        (NodeType.TEST, NodeType.CLAIM),
        (NodeType.TEST, NodeType.ISSUE),
        (NodeType.TEST, NodeType.POL),
    }),
    EdgeType.SUPPORTS: frozenset({
        (NodeType.ASN, NodeType.TEST),
        (NodeType.ART, NodeType.CLAIM),
        (NodeType.GRADE, NodeType.CLAIM),
        (NodeType.ANL, NodeType.CLAIM),
        (NodeType.ADR, NodeType.REQ),
    }),
    EdgeType.CONTRADICTS: frozenset({
        (NodeType.CLAIM, NodeType.CLAIM),
        (NodeType.ART, NodeType.CLAIM),
        (NodeType.GRADE, NodeType.CLAIM),
        (NodeType.ANL, NodeType.CLAIM),
        (NodeType.ANL, NodeType.ANL),
        (NodeType.DEC, NodeType.DEC),
    }),
    EdgeType.DERIVED_FROM: frozenset({
        (NodeType.ART, NodeType.RUN),
        (NodeType.GRADE, NodeType.ART),
        (NodeType.ANL, NodeType.OBJ),
        (NodeType.ANL, NodeType.GRADE),
        (NodeType.ANL, NodeType.ART),
        (NodeType.DEC, NodeType.ANL),
        (NodeType.CLAIM, NodeType.ANL),
        (NodeType.CLAIM, NodeType.GRADE),
        (NodeType.POL, NodeType.DEC),
        (NodeType.DEP, NodeType.POL),
    }),
    EdgeType.ASSIGNED_BY: frozenset({
        (NodeType.RUN, NodeType.ASN),
        (NodeType.TASK, NodeType.ASN),
    }),
    EdgeType.EVALUATES: frozenset({
        (NodeType.GRADE, NodeType.ART),
        (NodeType.GRADE, NodeType.RUN),
        (NodeType.ANL, NodeType.EXP),
    }),
    EdgeType.SUPERSEDES: frozenset({
        (node_type, node_type)
        for node_type in (
            NodeType.CLAIM,
            NodeType.ADR,
            NodeType.ART,
            NodeType.GRADE,
            NodeType.DEC,
            NodeType.POL,
            NodeType.DEP,
        )
    }),
    EdgeType.INVALIDATES: frozenset({
        (NodeType.ART, NodeType.ART),
        (NodeType.GRADE, NodeType.GRADE),
        (NodeType.ANL, NodeType.CLAIM),
        (NodeType.DEC, NodeType.CLAIM),
        (NodeType.DEC, NodeType.POL),
        (NodeType.POL, NodeType.POL),
    }),
    EdgeType.PROMOTES: frozenset({
        (NodeType.PROMOTION, NodeType.DEC),
        (NodeType.PROMOTION, NodeType.POL),
        (NodeType.DEP, NodeType.DEC),
    }),
    EdgeType.ROLLS_BACK: frozenset({
        (NodeType.DEP, NodeType.DEP),
        (NodeType.DEP, NodeType.POL),
        (NodeType.PROMOTION, NodeType.PROMOTION),
        (NodeType.DEC, NodeType.DEP),
    }),
}


class ProvKind(str, Enum):
    """W3C PROV top-level concepts used by the trace graph."""

    ENTITY = "entity"
    ACTIVITY = "activity"
    AGENT = "agent"


class ClosureRule(str, Enum):
    """Stable rule IDs for closure findings and waivers."""

    REQ_HAS_TEST = "req_has_test"
    TEST_HAS_RUNTIME_EVIDENCE = "test_has_runtime_evidence"
    TEST_HAS_PINNED_RUN = "test_has_pinned_run"
    EVIDENCE_HAS_VERIFIER_GRADE = "evidence_has_verifier_grade"
    GRADE_HAS_VERIFIER = "grade_has_verifier"
    DECISION_HAS_ANALYSIS = "decision_has_analysis"
    DECISION_CONTEXT_MATCHES = "decision_context_matches"
    DECISION_GRADE_CITATIONS_CURRENT = (
        "decision_grade_citations_current"
    )
    PROMOTION_HAS_DECISION = "promotion_has_decision"
    PROMOTION_PATH_HAS_PINNED_RUN = "promotion_path_has_pinned_run"
    NODE_REACHES_OBJECTIVE = "node_reaches_objective"


_PROV_KIND_BY_NODE_TYPE = {
    NodeType.OBJ: ProvKind.ENTITY,
    NodeType.CLAIM: ProvKind.ENTITY,
    NodeType.REQ: ProvKind.ENTITY,
    NodeType.ADR: ProvKind.ENTITY,
    NodeType.ISSUE: ProvKind.ENTITY,
    NodeType.TEST: ProvKind.ENTITY,
    NodeType.TASK: ProvKind.ACTIVITY,
    NodeType.EXP: ProvKind.ENTITY,
    NodeType.ASN: ProvKind.AGENT,
    NodeType.RUN: ProvKind.ACTIVITY,
    NodeType.ART: ProvKind.ENTITY,
    NodeType.GRADE: ProvKind.ENTITY,
    NodeType.ANL: ProvKind.ENTITY,
    NodeType.DEC: ProvKind.ENTITY,
    NodeType.POL: ProvKind.ENTITY,
    NodeType.DEP: ProvKind.ENTITY,
    NodeType.PROMOTION: ProvKind.ACTIVITY,
}


class TraceGraphError(ValueError):
    """Base error for invalid trace graph data."""


class InvalidTraceIdentity(TraceGraphError):
    """Raised when a node identity is ambiguous or non-canonical."""


class TracePathNotFound(TraceGraphError):
    """Raised when a promotion has no canonical path to an objective."""


def canonical_revision_hash(revision: Any) -> str:
    """Return a sha256 over a canonical representation of one revision."""
    if isinstance(revision, bytes):
        payload = revision
    elif isinstance(revision, str):
        payload = revision.encode("utf-8")
    else:
        payload = json.dumps(
            revision,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    return sha256(payload).hexdigest()


def new_trace_instance_id() -> str:
    """Return a dependency-free, monotonic RFC UUIDv7 instance identifier."""
    global _UUID7_LAST_RANDOM, _UUID7_LAST_TIMESTAMP_MS

    timestamp_ms = int(time.time_ns() // 1_000_000)
    with _UUID7_LOCK:
        if timestamp_ms > _UUID7_LAST_TIMESTAMP_MS:
            random_bits = secrets.randbits(74)
        else:
            timestamp_ms = _UUID7_LAST_TIMESTAMP_MS
            random_bits = (_UUID7_LAST_RANDOM + 1) & _UUID7_RANDOM_MASK
            if random_bits == 0:
                timestamp_ms += 1
        _UUID7_LAST_TIMESTAMP_MS = timestamp_ms
        _UUID7_LAST_RANDOM = random_bits
    return str(_uuid7_from_parts(timestamp_ms, random_bits))


def trace_instance_id_from_hash(
    *,
    timestamp_ms: int,
    content_hash: str,
    domain: str = "supervisor-trace-instance",
) -> str:
    """Derive a stable RFC UUIDv7 for an immutable hash-pinned record."""
    if isinstance(timestamp_ms, bool):
        raise TraceGraphError("UUIDv7 timestamp_ms must be an integer")
    try:
        exact_timestamp_ms = int(timestamp_ms)
    except (TypeError, ValueError) as exc:
        raise TraceGraphError("UUIDv7 timestamp_ms must be an integer") from exc
    digest = str(content_hash)
    if not _SHA256_RE.fullmatch(digest):
        raise TraceGraphError(
            "UUIDv7 content_hash must be a canonical lowercase sha256"
        )
    domain_text = str(domain).strip()
    if not domain_text:
        raise TraceGraphError("UUIDv7 derivation domain must be non-empty")
    entropy = sha256(
        f"{domain_text}\0{digest}".encode("utf-8")
    ).digest()
    random_bits = int.from_bytes(entropy, "big") & _UUID7_RANDOM_MASK
    return str(_uuid7_from_parts(exact_timestamp_ms, random_bits))


def _uuid7_from_parts(timestamp_ms: int, random_bits: int) -> UUID:
    if not 0 <= timestamp_ms <= _UUID7_TIMESTAMP_MAX:
        raise TraceGraphError("UUIDv7 timestamp_ms is outside the 48-bit range")
    if not 0 <= random_bits <= _UUID7_RANDOM_MASK:
        raise TraceGraphError("UUIDv7 random payload is outside the 74-bit range")
    rand_a = random_bits >> 62
    rand_b = random_bits & ((1 << 62) - 1)
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return UUID(int=value)


@dataclass(frozen=True)
class TraceIdentity:
    """Exact identity for one logical node revision and materialized instance."""

    namespace: str
    node_type: NodeType
    logical_id: str
    revision_hash: str
    instance_id: str

    def __post_init__(self) -> None:
        namespace = str(self.namespace)
        logical_id = str(self.logical_id)
        revision_hash = str(self.revision_hash)
        try:
            node_type = NodeType(self.node_type)
        except ValueError as exc:
            raise InvalidTraceIdentity(
                f"unsupported trace node type: {self.node_type}"
            ) from exc
        if not _NAMESPACE_RE.fullmatch(namespace):
            raise InvalidTraceIdentity(
                "namespace must be explicit and contain only letters, digits, "
                "'.', '_', '/', or '-'"
            )
        if _LEGACY_BARE_ID_RE.fullmatch(logical_id):
            raise InvalidTraceIdentity(
                "bare legacy IDs such as P1 are compatibility-only; use "
                "TraceIdentity.from_legacy with an explicit namespace"
            )
        if not _LOGICAL_ID_RE.fullmatch(logical_id):
            raise InvalidTraceIdentity(
                "logical_id must be explicit and contain no whitespace"
            )
        if not _SHA256_RE.fullmatch(revision_hash):
            raise InvalidTraceIdentity(
                "revision_hash must be a canonical lowercase sha256 hex digest"
            )
        try:
            parsed_instance_id = UUID(str(self.instance_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidTraceIdentity(
                "instance_id must be an RFC UUIDv7 value"
            ) from exc
        if (
            parsed_instance_id.version != 7
            or ((parsed_instance_id.int >> 62) & 0b11) != 0b10
        ):
            raise InvalidTraceIdentity(
                "instance_id must be an RFC UUIDv7 value"
            )
        canonical_instance_id = str(parsed_instance_id)

        object.__setattr__(self, "node_type", node_type)
        object.__setattr__(self, "instance_id", canonical_instance_id)

    @property
    def canonical_key(self) -> str:
        return (
            f"{self.namespace}:{self.node_type.value}:{self.logical_id}"
            f"@{self.revision_hash}#{self.instance_id}"
        )

    @classmethod
    def from_legacy(
        cls,
        *,
        namespace: str,
        node_type: NodeType,
        local_id: str,
        revision: Any,
        instance_id: str,
    ) -> "TraceIdentity":
        """Namespace a legacy local ID without accepting it as a new identity."""
        local_id = str(local_id).strip()
        if not _LEGACY_BARE_ID_RE.fullmatch(local_id):
            raise InvalidTraceIdentity(
                "legacy compatibility requires a bare promise ID such as P1"
            )
        typed_node = NodeType(node_type)
        return cls(
            namespace=namespace,
            node_type=typed_node,
            logical_id=f"{typed_node.value}-{namespace}:{local_id.upper()}",
            revision_hash=canonical_revision_hash(revision),
            instance_id=instance_id,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "namespace": self.namespace,
            "node_type": self.node_type.value,
            "logical_id": self.logical_id,
            "revision_hash": self.revision_hash,
            "instance_id": self.instance_id,
            "canonical_key": self.canonical_key,
        }


@dataclass(frozen=True)
class TracePlanningArtifactRef:
    """One exact planning artifact reference included in closure authority."""

    kind: str
    path: str
    sha256: str

    def __post_init__(self) -> None:
        kind = str(self.kind).strip()
        path = str(self.path).strip()
        digest = str(self.sha256).strip().lower()
        if not kind:
            raise TraceGraphError("planning artifact kind must be non-empty")
        if not path:
            raise TraceGraphError("planning artifact path must be non-empty")
        if not _SHA256_RE.fullmatch(digest):
            raise TraceGraphError(
                "planning artifact sha256 must be a canonical lowercase digest"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "sha256", digest)

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "TracePlanningArtifactRef":
        expected_keys = {"kind", "path", "sha256"}
        if set(value) != expected_keys:
            raise TraceGraphError(
                "planning artifact binding must contain exactly "
                "kind, path, and sha256"
            )
        return cls(
            kind=str(value.get("kind") or ""),
            path=str(value.get("path") or ""),
            sha256=str(value.get("sha256") or ""),
        )


@dataclass(frozen=True)
class TraceClosureBinding:
    """Exact workflow and planning inputs authorized by a trace decision."""

    task_id: str
    run_id: str
    gate: str
    planning_artifacts: tuple[TracePlanningArtifactRef, ...] = ()
    schema_version: str = TRACE_CLOSURE_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        task_id = str(self.task_id).strip()
        run_id = str(self.run_id).strip()
        gate = str(self.gate).strip()
        if not task_id:
            raise TraceGraphError("trace closure task_id must be non-empty")
        if not run_id:
            raise TraceGraphError("trace closure run_id must be non-empty")
        if not gate:
            raise TraceGraphError("trace closure gate must be non-empty")
        if self.schema_version != TRACE_CLOSURE_BINDING_SCHEMA_VERSION:
            raise TraceGraphError(
                "unsupported trace closure binding schema: "
                f"{self.schema_version}"
            )
        references = tuple(
            reference
            if isinstance(reference, TracePlanningArtifactRef)
            else TracePlanningArtifactRef.from_mapping(reference)
            for reference in self.planning_artifacts
        )
        identities = [(reference.kind, reference.path) for reference in references]
        if len(identities) != len(set(identities)):
            raise TraceGraphError(
                "trace closure planning artifact refs must be unique by kind/path"
            )
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "gate", gate)
        object.__setattr__(
            self,
            "planning_artifacts",
            tuple(
                sorted(
                    references,
                    key=lambda reference: (
                        reference.kind,
                        reference.path,
                        reference.sha256,
                    ),
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "gate": self.gate,
            "planning_artifacts": [
                reference.to_dict()
                for reference in self.planning_artifacts
            ],
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "TraceClosureBinding":
        expected_keys = {
            "schema_version",
            "task_id",
            "run_id",
            "gate",
            "planning_artifacts",
        }
        if set(value) != expected_keys:
            raise TraceGraphError(
                "trace closure binding must contain exactly schema_version, "
                "task_id, run_id, gate, and planning_artifacts"
            )
        raw_references = value.get("planning_artifacts")
        if not isinstance(raw_references, (list, tuple)):
            raise TraceGraphError(
                "trace closure planning_artifacts must be a list"
            )
        if not all(
            isinstance(reference, Mapping)
            for reference in raw_references
        ):
            raise TraceGraphError(
                "trace closure planning artifact refs must be objects"
            )
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            task_id=str(value.get("task_id") or ""),
            run_id=str(value.get("run_id") or ""),
            gate=str(value.get("gate") or ""),
            planning_artifacts=tuple(
                TracePlanningArtifactRef.from_mapping(reference)
                for reference in raw_references
            ),
        )


@dataclass(frozen=True)
class TraceDecisionGradeValidation:
    """Storage-neutral result returned by an injected grade authority."""

    accepted: bool
    blockers: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        normalized = tuple(
            _freeze_json(
                _normalise_json(blocker, field="grade validation blocker")
            )
            for blocker in self.blockers
        )
        object.__setattr__(self, "accepted", bool(self.accepted))
        object.__setattr__(self, "blockers", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "blockers": [
                _thaw_json(blocker)
                for blocker in self.blockers
            ],
        }


class DecisionGradeValidator(Protocol):
    """Injected authoritative GradeBook-compatible citation registry."""

    def validate_decision(
        self,
        citations: Iterable[Any],
    ) -> Any:
        ...


@dataclass(frozen=True)
class TraceNode:
    """One typed PROV node with closure-relevant provenance pins."""

    identity: TraceIdentity
    pinned: bool = False
    runtime_evidence: bool = False
    verifier_id: str | None = None
    verifier_revision_hash: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.runtime_evidence and self.identity.node_type is not NodeType.ART:
            raise TraceGraphError("runtime_evidence is only valid for ART nodes")
        if self.pinned and self.identity.node_type is not NodeType.RUN:
            raise TraceGraphError("pinned is only valid for RUN nodes")
        verifier_id = (
            str(self.verifier_id).strip()
            if self.verifier_id is not None
            else None
        )
        verifier_hash = (
            str(self.verifier_revision_hash)
            if self.verifier_revision_hash is not None
            else None
        )
        if (verifier_id is None) != (verifier_hash is None):
            raise TraceGraphError(
                "verifier_id and verifier_revision_hash must be supplied together"
            )
        if verifier_id is not None:
            if self.identity.node_type is not NodeType.GRADE:
                raise TraceGraphError(
                    "verifier pins are only valid for GRADE nodes"
                )
            if not verifier_id:
                raise TraceGraphError("verifier_id must be non-empty")
            if not _SHA256_RE.fullmatch(verifier_hash or ""):
                raise TraceGraphError(
                    "verifier_revision_hash must be a canonical sha256"
                )
        object.__setattr__(self, "verifier_id", verifier_id)
        object.__setattr__(
            self,
            "attributes",
            _freeze_json(_normalise_json(self.attributes, field="attributes")),
        )

    @property
    def prov_kind(self) -> ProvKind:
        return _PROV_KIND_BY_NODE_TYPE[self.identity.node_type]

    @property
    def has_pinned_verifier(self) -> bool:
        return bool(self.verifier_id and self.verifier_revision_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "prov_kind": self.prov_kind.value,
            "pinned": self.pinned,
            "runtime_evidence": self.runtime_evidence,
            "verifier_id": self.verifier_id,
            "verifier_revision_hash": self.verifier_revision_hash,
            "attributes": _thaw_json(self.attributes),
        }


@dataclass(frozen=True)
class TraceEdge:
    """A directed relation from a downstream record to its prerequisite."""

    source: TraceIdentity
    relation: EdgeType
    target: TraceIdentity

    def __post_init__(self) -> None:
        try:
            relation = EdgeType(self.relation)
        except ValueError as exc:
            raise TraceGraphError(
                f"unsupported trace edge type: {self.relation}"
            ) from exc
        endpoints = (self.source.node_type, self.target.node_type)
        if endpoints not in _ALLOWED_EDGE_ENDPOINTS[relation]:
            raise TraceGraphError(
                "invalid trace edge semantics: "
                f"{self.source.node_type.value} --{relation.value}--> "
                f"{self.target.node_type.value}"
            )
        object.__setattr__(self, "relation", relation)

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source.canonical_key,
            "relation": self.relation.value,
            "target": self.target.canonical_key,
        }


@dataclass(frozen=True)
class ClosureFinding:
    rule: ClosureRule
    node: TraceIdentity
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule.value,
            "node": self.node.canonical_key,
            "message": self.message,
        }


@dataclass(frozen=True)
class TraceWaiver:
    """An exact rule+node waiver authenticated with HMAC-SHA256."""

    rule: ClosureRule
    node: TraceIdentity
    reason: str
    signed_by: str
    issued_at: datetime
    expires_at: datetime
    signature: str

    def __post_init__(self) -> None:
        try:
            rule = ClosureRule(self.rule)
        except ValueError as exc:
            raise TraceGraphError(
                f"unsupported closure rule: {self.rule}"
            ) from exc
        reason = str(self.reason).strip()
        signed_by = str(self.signed_by).strip()
        if not reason:
            raise TraceGraphError("waiver reason must be non-empty")
        if not signed_by:
            raise TraceGraphError("waiver signed_by must be non-empty")
        _require_explicit_now(self.issued_at)
        _require_explicit_now(self.expires_at)
        if self.expires_at <= self.issued_at:
            raise TraceGraphError("waiver expires_at must be after issued_at")
        object.__setattr__(self, "rule", rule)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "signed_by", signed_by)

    @classmethod
    def sign(
        cls,
        *,
        rule: ClosureRule,
        node: TraceIdentity,
        reason: str,
        signed_by: str,
        issued_at: datetime,
        expires_at: datetime,
        signing_key: bytes | str,
    ) -> "TraceWaiver":
        payload = _waiver_payload(
            rule=ClosureRule(rule),
            node=node,
            reason=str(reason).strip(),
            signed_by=str(signed_by).strip(),
            issued_at=issued_at,
            expires_at=expires_at,
        )
        signature = "hmac-sha256:" + hmac.new(
            _key_bytes(signing_key),
            payload,
            sha256,
        ).hexdigest()
        return cls(
            rule=rule,
            node=node,
            reason=reason,
            signed_by=signed_by,
            issued_at=issued_at,
            expires_at=expires_at,
            signature=signature,
        )

    def is_valid(
        self,
        *,
        now: datetime,
        verification_keys: Mapping[str, bytes | str],
    ) -> bool:
        _require_explicit_now(now)
        if not self.issued_at <= now < self.expires_at:
            return False
        key = verification_keys.get(self.signed_by)
        if key is None:
            return False
        expected = "hmac-sha256:" + hmac.new(
            _key_bytes(key),
            _waiver_payload(
                rule=self.rule,
                node=self.node,
                reason=self.reason,
                signed_by=self.signed_by,
                issued_at=self.issued_at,
                expires_at=self.expires_at,
            ),
            sha256,
        ).hexdigest()
        return hmac.compare_digest(self.signature, expected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule.value,
            "node": self.node.canonical_key,
            "reason": self.reason,
            "signed_by": self.signed_by,
            "issued_at": _utc_iso(self.issued_at),
            "expires_at": _utc_iso(self.expires_at),
            "signature": self.signature,
        }


@dataclass(frozen=True)
class ClosureResult:
    findings: tuple[ClosureFinding, ...]
    waivers_used: tuple[TraceWaiver, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TRACE_GRAPH_SCHEMA_VERSION,
            "status": "accepted" if self.ok else "blocked",
            "findings": [finding.to_dict() for finding in self.findings],
            "waivers_used": [waiver.to_dict() for waiver in self.waivers_used],
        }


class TraceGraph:
    """In-memory typed trace graph with deterministic closure queries."""

    _OBJECTIVE_RELATIONS = frozenset({
        EdgeType.IMPLEMENTS,
        EdgeType.TESTS,
        EdgeType.SUPPORTS,
        EdgeType.DERIVED_FROM,
        EdgeType.ASSIGNED_BY,
        EdgeType.EVALUATES,
        EdgeType.SUPERSEDES,
        EdgeType.INVALIDATES,
        EdgeType.PROMOTES,
    })
    _PROMOTION_PATH = (
        (NodeType.PROMOTION, EdgeType.PROMOTES, NodeType.DEC),
        (NodeType.DEC, EdgeType.DERIVED_FROM, NodeType.ANL),
        (NodeType.ANL, EdgeType.DERIVED_FROM, NodeType.GRADE),
        (NodeType.GRADE, EdgeType.EVALUATES, NodeType.ART),
        (NodeType.ART, EdgeType.DERIVED_FROM, NodeType.RUN),
        (NodeType.RUN, EdgeType.ASSIGNED_BY, NodeType.ASN),
        (NodeType.ASN, EdgeType.SUPPORTS, NodeType.TEST),
        (NodeType.TEST, EdgeType.TESTS, NodeType.REQ),
        (NodeType.REQ, EdgeType.IMPLEMENTS, NodeType.OBJ),
    )

    def __init__(
        self,
        *,
        nodes: Iterable[TraceNode],
        edges: Iterable[TraceEdge] = (),
        waivers: Iterable[TraceWaiver] = (),
        decision_grade_validator: DecisionGradeValidator | None = None,
    ) -> None:
        self._nodes = tuple(nodes)
        self._edges = tuple(edges)
        self._waivers = tuple(waivers)
        self._expected_binding: TraceClosureBinding | None = None
        self._decision_grade_validator = decision_grade_validator
        self._by_identity: dict[TraceIdentity, TraceNode] = {}
        for node in self._nodes:
            if node.identity in self._by_identity:
                raise TraceGraphError(
                    f"duplicate trace identity: {node.identity.canonical_key}"
                )
            self._by_identity[node.identity] = node
        for edge in self._edges:
            if edge.source not in self._by_identity:
                raise TraceGraphError(
                    f"edge source is not in graph: {edge.source.canonical_key}"
                )
            if edge.target not in self._by_identity:
                raise TraceGraphError(
                    f"edge target is not in graph: {edge.target.canonical_key}"
                )
        self._outgoing: dict[TraceIdentity, list[TraceEdge]] = {}
        self._incoming: dict[TraceIdentity, list[TraceEdge]] = {}
        for edge in self._edges:
            self._outgoing.setdefault(edge.source, []).append(edge)
            self._incoming.setdefault(edge.target, []).append(edge)
        for adjacency in (self._outgoing, self._incoming):
            for identity in adjacency:
                adjacency[identity].sort(
                    key=lambda edge: (
                        edge.relation.value,
                        edge.source.canonical_key,
                        edge.target.canonical_key,
                    )
                )

    @property
    def nodes(self) -> tuple[TraceNode, ...]:
        return self._nodes

    @property
    def edges(self) -> tuple[TraceEdge, ...]:
        return self._edges

    @property
    def waivers(self) -> tuple[TraceWaiver, ...]:
        return self._waivers

    @property
    def expected_binding(self) -> TraceClosureBinding | None:
        """Return the caller-owned validation binding, if one was attached."""
        return self._expected_binding

    @property
    def decision_grade_validator(
        self,
    ) -> DecisionGradeValidator | None:
        """Return the injected grade authority attached at composition."""
        return self._decision_grade_validator

    def bind_validation(
        self,
        *,
        expected_binding: TraceClosureBinding,
        decision_grade_validator: DecisionGradeValidator | None = None,
    ) -> "TraceGraph":
        """Return an equivalent graph carrying caller-owned validation inputs."""
        if not isinstance(expected_binding, TraceClosureBinding):
            raise TraceGraphError(
                "trace validation binding must be a TraceClosureBinding"
            )
        bound = TraceGraph(
            nodes=self._nodes,
            edges=self._edges,
            waivers=self._waivers,
            decision_grade_validator=decision_grade_validator,
        )
        bound._expected_binding = expected_binding
        return bound

    def promotion_trace(
        self,
        promotion: TraceIdentity,
    ) -> tuple[TraceNode, ...]:
        start = self._by_identity.get(promotion)
        if start is None:
            raise TracePathNotFound(
                f"promotion is not in graph: {promotion.canonical_key}"
            )
        if promotion.node_type is not NodeType.PROMOTION:
            raise TracePathNotFound(
                f"trace start must be PROMOTION, got {promotion.node_type.value}"
            )
        dependency_path = self._find_promotion_path(
            promotion,
            step_index=0,
            seen=frozenset(),
        )
        if dependency_path is None:
            raise TracePathNotFound(
                f"no objective closure path for {promotion.canonical_key}"
            )
        return tuple(
            self._by_identity[identity]
            for identity in reversed(dependency_path)
        )

    def validate_closure(
        self,
        *,
        now: datetime,
        waiver_keys: Mapping[str, bytes | str] | None = None,
        expected_binding: TraceClosureBinding | None = None,
        decision_grade_validator: DecisionGradeValidator | None = None,
    ) -> ClosureResult:
        _require_explicit_now(now)
        if not self._nodes:
            raise TraceGraphError("trace graph has no nodes")
        validation_binding = expected_binding or self._expected_binding
        grade_validator = (
            decision_grade_validator
            if decision_grade_validator is not None
            else self._decision_grade_validator
        )
        findings: list[ClosureFinding] = []
        hard_findings: list[ClosureFinding] = []
        decision_identities = {
            node.identity
            for node in self._nodes
            if node.identity.node_type is NodeType.DEC
        }
        promoted_decisions = {
            edge.target
            for edge in self._edges
            if edge.relation is EdgeType.PROMOTES
            and edge.source.node_type is NodeType.PROMOTION
            and edge.target.node_type is NodeType.DEC
        }
        authoritative_decisions = promoted_decisions or decision_identities
        if validation_binding is not None and not authoritative_decisions:
            hard_findings.append(
                ClosureFinding(
                    rule=ClosureRule.DECISION_CONTEXT_MATCHES,
                    node=min(
                        self._nodes,
                        key=lambda candidate: (
                            candidate.identity.canonical_key
                        ),
                    ).identity,
                    message=(
                        "trace graph has no decision carrying the expected "
                        "task, run, gate, and planning artifact binding"
                    ),
                )
            )
        for node in self._nodes:
            node_type = node.identity.node_type
            if node_type is NodeType.REQ:
                tests = self._incoming_edges(
                    node.identity,
                    relation=EdgeType.TESTS,
                    source_type=NodeType.TEST,
                )
                if not tests:
                    findings.append(ClosureFinding(
                        rule=ClosureRule.REQ_HAS_TEST,
                        node=node.identity,
                        message="requirement has no testing TEST node",
                    ))
            elif node_type is NodeType.TEST:
                runs, evidence, pinned_evidence = (
                    self._runtime_support_for_test(node.identity)
                )
                if not evidence:
                    findings.append(ClosureFinding(
                        rule=ClosureRule.TEST_HAS_RUNTIME_EVIDENCE,
                        node=node.identity,
                        message=(
                            "test has no runtime ART evidence through "
                            "ASN and RUN"
                        ),
                    ))
                if (
                    not any(run.pinned for run in runs)
                    or (evidence and not pinned_evidence)
                ):
                    findings.append(ClosureFinding(
                        rule=ClosureRule.TEST_HAS_PINNED_RUN,
                        node=node.identity,
                        message=(
                            "test has no pinned RUN supplying its runtime "
                            "ART evidence through an ASN"
                        ),
                    ))
            elif node_type is NodeType.ART and node.runtime_evidence:
                grades = [
                    self._by_identity[edge.source]
                    for edge in self._incoming_edges(
                        node.identity,
                        relation=EdgeType.EVALUATES,
                        source_type=NodeType.GRADE,
                    )
                ]
                if not any(grade.has_pinned_verifier for grade in grades):
                    findings.append(ClosureFinding(
                        rule=ClosureRule.EVIDENCE_HAS_VERIFIER_GRADE,
                        node=node.identity,
                        message=(
                            "runtime evidence has no evaluating GRADE "
                            "with a pinned verifier"
                        ),
                    ))
            elif node_type is NodeType.GRADE:
                if not node.has_pinned_verifier:
                    findings.append(ClosureFinding(
                        rule=ClosureRule.GRADE_HAS_VERIFIER,
                        node=node.identity,
                        message="grade has no pinned verifier",
                    ))
            elif node_type is NodeType.DEC:
                analyses = self._outgoing_edges(
                    node.identity,
                    relation=EdgeType.DERIVED_FROM,
                    target_type=NodeType.ANL,
                )
                if not analyses:
                    findings.append(ClosureFinding(
                        rule=ClosureRule.DECISION_HAS_ANALYSIS,
                        node=node.identity,
                        message="decision has no supporting ANL node",
                    ))
                if (
                    validation_binding is not None
                    and node.identity in authoritative_decisions
                ):
                    hard_findings.extend(
                        self._decision_binding_findings(
                            node,
                            expected_binding=validation_binding,
                        )
                    )
                if node.identity in authoritative_decisions:
                    hard_findings.extend(
                        self._decision_grade_findings(
                            node,
                            validator=grade_validator,
                        )
                    )
            elif node_type is NodeType.PROMOTION:
                decisions = self._outgoing_edges(
                    node.identity,
                    relation=EdgeType.PROMOTES,
                    target_type=NodeType.DEC,
                )
                if not decisions:
                    findings.append(ClosureFinding(
                        rule=ClosureRule.PROMOTION_HAS_DECISION,
                        node=node.identity,
                        message="promotion has no authorizing DEC node",
                    ))
                else:
                    promotion_paths = self._find_promotion_paths(
                        node.identity,
                        step_index=0,
                        seen=frozenset(),
                    )
                    if not promotion_paths:
                        findings.append(ClosureFinding(
                            rule=ClosureRule.NODE_REACHES_OBJECTIVE,
                            node=node.identity,
                            message=(
                                "promotion has no canonical path through DEC, "
                                "ANL, GRADE, ART, RUN, ASN, TEST, REQ, and OBJ"
                            ),
                        ))
                    else:
                        unpinned_runs = sorted({
                            identity.canonical_key
                            for path in promotion_paths
                            for identity in path
                            if identity.node_type is NodeType.RUN
                            and not self._by_identity[identity].pinned
                        })
                        if unpinned_runs:
                            findings.append(ClosureFinding(
                                rule=(
                                    ClosureRule.PROMOTION_PATH_HAS_PINNED_RUN
                                ),
                                node=node.identity,
                                message=(
                                    "promotion dependency path contains "
                                    "unpinned RUN nodes: "
                                    + ", ".join(unpinned_runs)
                                ),
                            ))

        remaining_specific, used_specific = self._apply_waivers(
            findings,
            now=now,
            waiver_keys=waiver_keys or {},
        )
        nodes_with_specific_findings = {
            finding.node for finding in remaining_specific
        }
        uncovered = [
            ClosureFinding(
                rule=ClosureRule.NODE_REACHES_OBJECTIVE,
                node=node.identity,
                message="node has no directed dependency path to an OBJ node",
            )
            for node in self._nodes
            if node.identity.node_type
            not in {NodeType.OBJ, NodeType.DEC, NodeType.PROMOTION}
            and node.identity not in nodes_with_specific_findings
            and not self._reaches_objective(node.identity)
        ]
        remaining_uncovered, used_uncovered = self._apply_waivers(
            uncovered,
            now=now,
            waiver_keys=waiver_keys or {},
        )
        remaining = tuple(sorted(
            (
                *remaining_specific,
                *remaining_uncovered,
                *hard_findings,
            ),
            key=lambda finding: (
                finding.node.canonical_key,
                finding.rule.value,
            ),
        ))
        used = tuple(dict.fromkeys((*used_specific, *used_uncovered)))
        return ClosureResult(findings=remaining, waivers_used=used)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TRACE_GRAPH_SCHEMA_VERSION,
            "edge_direction": "source_record_to_prerequisite",
            "nodes": [node.to_dict() for node in self._nodes],
            "edges": [edge.to_dict() for edge in self._edges],
            "waivers": [waiver.to_dict() for waiver in self._waivers],
        }

    def canonical_bytes(self) -> bytes:
        """Return deterministic bytes suitable for persistence comparisons."""
        return _canonical_json(self.to_dict()).encode("utf-8")

    def _decision_binding_findings(
        self,
        decision: TraceNode,
        *,
        expected_binding: TraceClosureBinding,
    ) -> tuple[ClosureFinding, ...]:
        raw_binding = decision.attributes.get(
            TRACE_CLOSURE_BINDING_ATTRIBUTE
        )
        if not isinstance(raw_binding, Mapping):
            return (
                ClosureFinding(
                    rule=ClosureRule.DECISION_CONTEXT_MATCHES,
                    node=decision.identity,
                    message=(
                        "decision does not contain an exact "
                        f"{TRACE_CLOSURE_BINDING_ATTRIBUTE}"
                    ),
                ),
            )
        try:
            observed_binding = TraceClosureBinding.from_mapping(raw_binding)
        except TraceGraphError as exc:
            return (
                ClosureFinding(
                    rule=ClosureRule.DECISION_CONTEXT_MATCHES,
                    node=decision.identity,
                    message=f"decision trace closure binding is invalid: {exc}",
                ),
            )
        if observed_binding == expected_binding:
            return ()
        return (
            ClosureFinding(
                rule=ClosureRule.DECISION_CONTEXT_MATCHES,
                node=decision.identity,
                message=(
                    "decision trace closure binding differs from the expected "
                    "task, run, gate, or planning artifact refs/hashes: "
                    f"expected={_canonical_json(expected_binding.to_dict())};"
                    f"observed={_canonical_json(observed_binding.to_dict())}"
                ),
            ),
        )

    def _decision_grade_findings(
        self,
        decision: TraceNode,
        *,
        validator: DecisionGradeValidator | None,
    ) -> tuple[ClosureFinding, ...]:
        raw_citations = decision.attributes.get("grade_citations")
        if (
            not isinstance(raw_citations, (list, tuple))
            or not raw_citations
            or not all(
                isinstance(citation, Mapping)
                for citation in raw_citations
            )
        ):
            return (
                ClosureFinding(
                    rule=(
                        ClosureRule.DECISION_GRADE_CITATIONS_CURRENT
                    ),
                    node=decision.identity,
                    message=(
                        "decision grade_citations must be a non-empty list "
                        "of citation objects"
                    ),
                ),
            )
        if validator is None:
            return (
                ClosureFinding(
                    rule=(
                        ClosureRule.DECISION_GRADE_CITATIONS_CURRENT
                    ),
                    node=decision.identity,
                    message=(
                        "decision grade citations require an injected "
                        "grade-citation validator"
                    ),
                ),
            )
        citations = tuple(
            _normalise_json(
                citation,
                field="decision grade citation",
            )
            for citation in raw_citations
        )
        try:
            from .grade_revisions import (
                DecisionGradeCitation,
                GradeBook,
            )

            if not isinstance(validator, GradeBook):
                raise TypeError(
                    "decision grade authority must be an authoritative "
                    "GradeBook"
                )
            gradebook_citations = tuple(
                DecisionGradeCitation.from_mapping(citation)
                for citation in citations
            )
            authoritative_validation = validator.validate_decision(
                gradebook_citations
            )
            validation = TraceDecisionGradeValidation(
                accepted=authoritative_validation.accepted,
                blockers=tuple(
                    blocker.to_dict()
                    for blocker in authoritative_validation.blockers
                ),
            )
        except Exception as exc:
            return (
                ClosureFinding(
                    rule=(
                        ClosureRule.DECISION_GRADE_CITATIONS_CURRENT
                    ),
                    node=decision.identity,
                    message=(
                        "decision grade citation validation failed closed: "
                        f"{exc}"
                    ),
                ),
            )
        if not isinstance(validation, TraceDecisionGradeValidation):
            return (
                ClosureFinding(
                    rule=(
                        ClosureRule.DECISION_GRADE_CITATIONS_CURRENT
                    ),
                    node=decision.identity,
                    message=(
                        "decision grade validator returned an unsupported "
                        "result type"
                    ),
                ),
            )
        if not validation.accepted:
            return (
                ClosureFinding(
                    rule=ClosureRule.DECISION_GRADE_CITATIONS_CURRENT,
                    node=decision.identity,
                    message=(
                        "decision cites a superseded, invalidated, or otherwise "
                        "unacceptable grade revision: "
                        f"{_canonical_json(validation.to_dict())}"
                    ),
                ),
            )
        effective_citations = [
            (
                citation.resolution_grade_id or citation.grade_id,
                citation.resolution_revision_hash or citation.revision_hash,
            )
            for citation in gradebook_citations
        ]
        expected_grade_citations = self._decision_supporting_grade_citations(
            decision
        )
        if (
            len(effective_citations) != len(set(effective_citations))
            or set(effective_citations) != expected_grade_citations
        ):
            return (
                ClosureFinding(
                    rule=(
                        ClosureRule.DECISION_GRADE_CITATIONS_CURRENT
                    ),
                    node=decision.identity,
                    message=(
                        "decision grade citations must exactly cover every "
                        "GRADE revision on its authorizing analysis path"
                    ),
                ),
            )
        return ()

    def _decision_supporting_grade_citations(
        self,
        decision: TraceNode,
    ) -> set[tuple[str, str]]:
        citations: set[tuple[str, str]] = set()
        analyses = self._outgoing_edges(
            decision.identity,
            relation=EdgeType.DERIVED_FROM,
            target_type=NodeType.ANL,
        )
        for analysis_edge in analyses:
            grade_edges = self._outgoing_edges(
                analysis_edge.target,
                relation=EdgeType.DERIVED_FROM,
                target_type=NodeType.GRADE,
            )
            for grade_edge in grade_edges:
                grade = self._by_identity[grade_edge.target]
                grade_id = str(
                    grade.attributes.get("grade_id") or ""
                ).strip()
                revision_hash = str(
                    grade.attributes.get("grade_revision_hash")
                    or grade.identity.revision_hash
                ).strip()
                if (
                    not grade_id
                    or not _SHA256_RE.fullmatch(revision_hash)
                    or revision_hash != grade.identity.revision_hash
                ):
                    return set()
                citations.add((grade_id, revision_hash))
        return citations

    def _find_promotion_path(
        self,
        current: TraceIdentity,
        *,
        step_index: int,
        seen: frozenset[TraceIdentity],
    ) -> tuple[TraceIdentity, ...] | None:
        paths = self._find_promotion_paths(
            current,
            step_index=step_index,
            seen=seen,
        )
        return paths[0] if paths else None

    def _find_promotion_paths(
        self,
        current: TraceIdentity,
        *,
        step_index: int,
        seen: frozenset[TraceIdentity],
    ) -> tuple[tuple[TraceIdentity, ...], ...]:
        if step_index == len(self._PROMOTION_PATH):
            return ((current,),)
        expected_source, relation, expected_target = self._PROMOTION_PATH[
            step_index
        ]
        if current.node_type is not expected_source or current in seen:
            return ()
        candidates = [
            edge.target
            for edge in self._outgoing.get(current, ())
            if edge.relation is relation
            and edge.target.node_type is expected_target
        ]
        paths: list[tuple[TraceIdentity, ...]] = []
        for target in candidates:
            suffixes = self._find_promotion_paths(
                target,
                step_index=step_index + 1,
                seen=seen | {current},
            )
            paths.extend(
                (current, *suffix)
                for suffix in suffixes
            )
        return tuple(paths)

    def _incoming_edges(
        self,
        target: TraceIdentity,
        *,
        relation: EdgeType,
        source_type: NodeType,
    ) -> tuple[TraceEdge, ...]:
        return tuple(
            edge
            for edge in self._incoming.get(target, ())
            if edge.relation is relation
            and edge.source.node_type is source_type
        )

    def _outgoing_edges(
        self,
        source: TraceIdentity,
        *,
        relation: EdgeType,
        target_type: NodeType,
    ) -> tuple[TraceEdge, ...]:
        return tuple(
            edge
            for edge in self._outgoing.get(source, ())
            if edge.relation is relation
            and edge.target.node_type is target_type
        )

    def _runtime_support_for_test(
        self,
        test: TraceIdentity,
    ) -> tuple[
        tuple[TraceNode, ...],
        tuple[TraceNode, ...],
        tuple[TraceNode, ...],
    ]:
        assignments = [
            edge.source
            for edge in self._incoming_edges(
                test,
                relation=EdgeType.SUPPORTS,
                source_type=NodeType.ASN,
            )
        ]
        runs: list[TraceNode] = []
        evidence: list[TraceNode] = []
        pinned_evidence: list[TraceNode] = []
        for assignment in assignments:
            for run_edge in self._incoming_edges(
                assignment,
                relation=EdgeType.ASSIGNED_BY,
                source_type=NodeType.RUN,
            ):
                run = self._by_identity[run_edge.source]
                runs.append(run)
                for evidence_edge in self._incoming_edges(
                    run.identity,
                    relation=EdgeType.DERIVED_FROM,
                    source_type=NodeType.ART,
                ):
                    artifact = self._by_identity[evidence_edge.source]
                    if artifact.runtime_evidence:
                        evidence.append(artifact)
                        if run.pinned:
                            pinned_evidence.append(artifact)
        return tuple(runs), tuple(evidence), tuple(pinned_evidence)

    def _reaches_objective(self, start: TraceIdentity) -> bool:
        pending = [start]
        seen: set[TraceIdentity] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current.node_type is NodeType.OBJ:
                return True
            pending.extend(
                edge.target
                for edge in self._outgoing.get(current, ())
                if edge.relation in self._OBJECTIVE_RELATIONS
            )
        return False

    def _apply_waivers(
        self,
        findings: Iterable[ClosureFinding],
        *,
        now: datetime,
        waiver_keys: Mapping[str, bytes | str],
    ) -> tuple[tuple[ClosureFinding, ...], tuple[TraceWaiver, ...]]:
        valid = [
            waiver
            for waiver in self._waivers
            if waiver.is_valid(
                now=now,
                verification_keys=waiver_keys,
            )
        ]
        remaining: list[ClosureFinding] = []
        used: list[TraceWaiver] = []
        for finding in findings:
            waiver = next(
                (
                    candidate
                    for candidate in valid
                    if candidate.rule is finding.rule
                    and candidate.node == finding.node
                ),
                None,
            )
            if waiver is None:
                remaining.append(finding)
            elif waiver not in used:
                used.append(waiver)
        return tuple(remaining), tuple(used)


class TraceGraphStore:
    """Append-only SQLite persistence for one incrementally built graph."""

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
        self._initialise_schema()

    def __enter__(self) -> "TraceGraphStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def append(self, graph: TraceGraph) -> None:
        """Append new immutable records; identical records are idempotent."""
        if not isinstance(graph, TraceGraph):
            raise TraceGraphError("TraceGraphStore.append requires a TraceGraph")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for node in graph.nodes:
                    self._append_node(node)
                for edge in graph.edges:
                    self._append_edge(edge)
                for waiver in graph.waivers:
                    self._append_waiver(waiver)
                self._conn.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                self._conn.execute("ROLLBACK")
                raise TraceGraphError(
                    f"failed to append immutable trace graph records: {exc}"
                ) from exc
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def append_graph(self, graph: TraceGraph) -> None:
        """Compatibility alias for callers that prefer an explicit noun."""
        self.append(graph)

    def load(self) -> TraceGraph:
        """Reload the complete graph in original append order."""
        with self._lock:
            node_rows = self._conn.execute(
                """
                SELECT canonical_key, payload_json, payload_hash
                FROM trace_nodes
                ORDER BY node_sequence ASC
                """
            ).fetchall()
            edge_rows = self._conn.execute(
                """
                SELECT edge_key, payload_json, payload_hash
                FROM trace_edges
                ORDER BY edge_sequence ASC
                """
            ).fetchall()
            waiver_rows = self._conn.execute(
                """
                SELECT waiver_key, payload_json, payload_hash
                FROM trace_waivers
                ORDER BY waiver_sequence ASC
                """
            ).fetchall()

        nodes = tuple(
            _trace_node_from_payload(
                _decode_stored_payload(row, record_kind="node")
            )
            for row in node_rows
        )
        identities = {
            node.identity.canonical_key: node.identity for node in nodes
        }
        edges = tuple(
            _trace_edge_from_payload(
                _decode_stored_payload(row, record_kind="edge"),
                identities=identities,
            )
            for row in edge_rows
        )
        waivers = tuple(
            _trace_waiver_from_payload(
                _decode_stored_payload(row, record_kind="waiver"),
                identities=identities,
            )
            for row in waiver_rows
        )
        return TraceGraph(nodes=nodes, edges=edges, waivers=waivers)

    def load_graph(self) -> TraceGraph:
        """Compatibility alias for callers that prefer an explicit noun."""
        return self.load()

    def _append_node(self, node: TraceNode) -> None:
        payload_json = _canonical_json(node.to_dict())
        payload_hash = _payload_hash(payload_json)
        self._insert_or_verify(
            table="trace_nodes",
            key_column="canonical_key",
            key=node.identity.canonical_key,
            payload_json=payload_json,
            payload_hash=payload_hash,
            insert_sql="""
                INSERT INTO trace_nodes(
                  canonical_key, payload_json, payload_hash
                ) VALUES(?, ?, ?)
            """,
            insert_values=(
                node.identity.canonical_key,
                payload_json,
                payload_hash,
            ),
        )

    def _append_edge(self, edge: TraceEdge) -> None:
        payload_json = _canonical_json(edge.to_dict())
        payload_hash = _payload_hash(payload_json)
        self._insert_or_verify(
            table="trace_edges",
            key_column="edge_key",
            key=payload_hash,
            payload_json=payload_json,
            payload_hash=payload_hash,
            insert_sql="""
                INSERT INTO trace_edges(
                  edge_key, source_key, relation, target_key,
                  payload_json, payload_hash
                ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                payload_hash,
                edge.source.canonical_key,
                edge.relation.value,
                edge.target.canonical_key,
                payload_json,
                payload_hash,
            ),
        )

    def _append_waiver(self, waiver: TraceWaiver) -> None:
        payload_json = _canonical_json(waiver.to_dict())
        payload_hash = _payload_hash(payload_json)
        self._insert_or_verify(
            table="trace_waivers",
            key_column="waiver_key",
            key=payload_hash,
            payload_json=payload_json,
            payload_hash=payload_hash,
            insert_sql="""
                INSERT INTO trace_waivers(
                  waiver_key, node_key, payload_json, payload_hash
                ) VALUES(?, ?, ?, ?)
            """,
            insert_values=(
                payload_hash,
                waiver.node.canonical_key,
                payload_json,
                payload_hash,
            ),
        )

    def _insert_or_verify(
        self,
        *,
        table: str,
        key_column: str,
        key: str,
        payload_json: str,
        payload_hash: str,
        insert_sql: str,
        insert_values: tuple[object, ...],
    ) -> None:
        row = self._conn.execute(
            f"""
            SELECT payload_json, payload_hash
            FROM {table}
            WHERE {key_column}=?
            """,
            (key,),
        ).fetchone()
        if row is None:
            self._conn.execute(insert_sql, insert_values)
            return
        stored_payload = str(row["payload_json"])
        stored_hash = str(row["payload_hash"])
        if (
            stored_payload != payload_json
            or stored_hash != payload_hash
            or _payload_hash(stored_payload) != stored_hash
        ):
            raise TraceGraphError(
                f"immutable trace record conflict in {table}: {key}"
            )

    def _initialise_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trace_store_metadata (
                  schema_version TEXT PRIMARY KEY
                );

                INSERT OR IGNORE INTO trace_store_metadata(schema_version)
                VALUES('supervisor-trace-graph-store/v1');

                CREATE TABLE IF NOT EXISTS trace_nodes (
                  node_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  canonical_key TEXT NOT NULL UNIQUE,
                  payload_json TEXT NOT NULL,
                  payload_hash TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trace_edges (
                  edge_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  edge_key TEXT NOT NULL UNIQUE,
                  source_key TEXT NOT NULL REFERENCES trace_nodes(canonical_key),
                  relation TEXT NOT NULL,
                  target_key TEXT NOT NULL REFERENCES trace_nodes(canonical_key),
                  payload_json TEXT NOT NULL,
                  payload_hash TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trace_waivers (
                  waiver_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  waiver_key TEXT NOT NULL UNIQUE,
                  node_key TEXT NOT NULL REFERENCES trace_nodes(canonical_key),
                  payload_json TEXT NOT NULL,
                  payload_hash TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS trace_store_metadata_no_update
                BEFORE UPDATE ON trace_store_metadata
                BEGIN
                  SELECT RAISE(ABORT, 'trace store metadata is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS trace_store_metadata_no_delete
                BEFORE DELETE ON trace_store_metadata
                BEGIN
                  SELECT RAISE(ABORT, 'trace store metadata is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS trace_nodes_no_update
                BEFORE UPDATE ON trace_nodes
                BEGIN
                  SELECT RAISE(ABORT, 'trace nodes are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS trace_nodes_no_delete
                BEFORE DELETE ON trace_nodes
                BEGIN
                  SELECT RAISE(ABORT, 'trace nodes are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS trace_edges_no_update
                BEFORE UPDATE ON trace_edges
                BEGIN
                  SELECT RAISE(ABORT, 'trace edges are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS trace_edges_no_delete
                BEFORE DELETE ON trace_edges
                BEGIN
                  SELECT RAISE(ABORT, 'trace edges are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS trace_waivers_no_update
                BEFORE UPDATE ON trace_waivers
                BEGIN
                  SELECT RAISE(ABORT, 'trace waivers are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS trace_waivers_no_delete
                BEFORE DELETE ON trace_waivers
                BEGIN
                  SELECT RAISE(ABORT, 'trace waivers are immutable');
                END;
                """
            )


def _decode_stored_payload(
    row: sqlite3.Row,
    *,
    record_kind: str,
) -> Mapping[str, Any]:
    payload_json = str(row["payload_json"])
    payload_hash = str(row["payload_hash"])
    if not _SHA256_RE.fullmatch(payload_hash):
        raise TraceGraphError(
            f"stored trace {record_kind} has an invalid payload hash"
        )
    if _payload_hash(payload_json) != payload_hash:
        raise TraceGraphError(
            f"stored trace {record_kind} payload hash mismatch"
        )
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise TraceGraphError(
            f"stored trace {record_kind} is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise TraceGraphError(
            f"stored trace {record_kind} payload must be an object"
        )
    return payload


def _trace_identity_from_payload(payload: object) -> TraceIdentity:
    if not isinstance(payload, Mapping):
        raise TraceGraphError("stored trace identity must be an object")
    try:
        identity = TraceIdentity(
            namespace=str(payload["namespace"]),
            node_type=NodeType(str(payload["node_type"])),
            logical_id=str(payload["logical_id"]),
            revision_hash=str(payload["revision_hash"]),
            instance_id=str(payload["instance_id"]),
        )
    except KeyError as exc:
        raise TraceGraphError(
            f"stored trace identity is missing {exc.args[0]}"
        ) from exc
    recorded_key = payload.get("canonical_key")
    if recorded_key is not None and str(recorded_key) != identity.canonical_key:
        raise TraceGraphError("stored trace identity canonical key mismatch")
    return identity


def _trace_node_from_payload(payload: Mapping[str, Any]) -> TraceNode:
    try:
        identity_payload = payload["identity"]
        pinned = payload["pinned"]
        runtime_evidence = payload["runtime_evidence"]
        attributes = payload["attributes"]
    except KeyError as exc:
        raise TraceGraphError(
            f"stored trace node is missing {exc.args[0]}"
        ) from exc
    if not isinstance(pinned, bool) or not isinstance(runtime_evidence, bool):
        raise TraceGraphError("stored trace node flags must be booleans")
    if not isinstance(attributes, Mapping):
        raise TraceGraphError("stored trace node attributes must be an object")
    node = TraceNode(
        identity=_trace_identity_from_payload(identity_payload),
        pinned=pinned,
        runtime_evidence=runtime_evidence,
        verifier_id=(
            str(payload["verifier_id"])
            if payload.get("verifier_id") is not None
            else None
        ),
        verifier_revision_hash=(
            str(payload["verifier_revision_hash"])
            if payload.get("verifier_revision_hash") is not None
            else None
        ),
        attributes=attributes,
    )
    recorded_prov_kind = payload.get("prov_kind")
    if (
        recorded_prov_kind is not None
        and str(recorded_prov_kind) != node.prov_kind.value
    ):
        raise TraceGraphError("stored trace node PROV kind mismatch")
    return node


def _trace_edge_from_payload(
    payload: Mapping[str, Any],
    *,
    identities: Mapping[str, TraceIdentity],
) -> TraceEdge:
    try:
        source_key = str(payload["source"])
        target_key = str(payload["target"])
        relation = EdgeType(str(payload["relation"]))
    except KeyError as exc:
        raise TraceGraphError(
            f"stored trace edge is missing {exc.args[0]}"
        ) from exc
    try:
        source = identities[source_key]
        target = identities[target_key]
    except KeyError as exc:
        raise TraceGraphError(
            f"stored trace edge references an unknown node: {exc.args[0]}"
        ) from exc
    return TraceEdge(source=source, relation=relation, target=target)


def _trace_waiver_from_payload(
    payload: Mapping[str, Any],
    *,
    identities: Mapping[str, TraceIdentity],
) -> TraceWaiver:
    try:
        node_key = str(payload["node"])
        node = identities[node_key]
        return TraceWaiver(
            rule=ClosureRule(str(payload["rule"])),
            node=node,
            reason=str(payload["reason"]),
            signed_by=str(payload["signed_by"]),
            issued_at=_parse_utc_iso(str(payload["issued_at"])),
            expires_at=_parse_utc_iso(str(payload["expires_at"])),
            signature=str(payload["signature"]),
        )
    except KeyError as exc:
        raise TraceGraphError(
            f"stored trace waiver is missing or references unknown {exc.args[0]}"
        ) from exc


def _require_explicit_now(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TraceGraphError("closure validation requires an explicit aware now")
    if now.utcoffset() is None:
        raise TraceGraphError("closure validation requires an explicit aware now")


def _waiver_payload(
    *,
    rule: ClosureRule,
    node: TraceIdentity,
    reason: str,
    signed_by: str,
    issued_at: datetime,
    expires_at: datetime,
) -> bytes:
    _require_explicit_now(issued_at)
    _require_explicit_now(expires_at)
    return json.dumps(
        {
            "schema_version": "supervisor-trace-waiver/v1",
            "rule": rule.value,
            "node": node.to_dict(),
            "reason": reason,
            "signed_by": signed_by,
            "issued_at": _utc_iso(issued_at),
            "expires_at": _utc_iso(expires_at),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _key_bytes(key: bytes | str) -> bytes:
    value = key if isinstance(key, bytes) else str(key).encode("utf-8")
    if not value:
        raise TraceGraphError("waiver signing key must be non-empty")
    return value


def _utc_iso(value: datetime) -> str:
    _require_explicit_now(value)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_utc_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TraceGraphError("stored trace timestamp is not valid ISO-8601") from exc
    _require_explicit_now(parsed)
    if _utc_iso(parsed) != value:
        raise TraceGraphError("stored trace timestamp is not canonical UTC")
    return parsed


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _payload_hash(payload_json: str) -> str:
    return sha256(payload_json.encode("utf-8")).hexdigest()


def _normalise_json(value: object, *, field: str) -> Any:
    try:
        thawed = _thaw_json(value)
        _validate_json_tree(thawed, path=field)
        return json.loads(_canonical_json(thawed))
    except (TypeError, ValueError) as exc:
        raise TraceGraphError(
            f"{field} must contain canonical JSON-compatible values"
        ) from exc


def _validate_json_tree(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TraceGraphError(
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
    raise TraceGraphError(
        f"{path} contains a non-JSON value of type {type(value).__name__}"
    )


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
