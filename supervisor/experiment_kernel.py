"""Preregistered A/B/C task experiment enforcement kernel."""
from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import math
import re
import sqlite3
import time
from collections.abc import Iterator, Mapping as MappingABC, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .grade_revisions import (
    GradeBook,
    GradeRevision,
    RunEnvelopeRef,
)
from .task_environment import (
    GRADE_SCHEMA_VERSION,
    FrozenTaskResult,
    Grade,
    TaskSpec,
    VerifierAdapter,
    bind_frozen_result_to_task,
    canonical_task_identity,
)


class Arm(str, Enum):
    A = "production_baseline"
    B = "supervisor"
    C = "compute_matched_direct"


ARM_ORDERS: tuple[tuple[Arm, Arm, Arm], ...] = tuple(
    itertools.permutations((Arm.A, Arm.B, Arm.C))
)
COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE = (
    "common_pre_treatment_infrastructure_failure"
)
ARM_EXECUTION_RECEIPT_SCHEMA_VERSION = "supervisor-arm-execution-receipt/v1"
ISOLATION_ATTESTATION_SCHEMA_VERSION = "supervisor-isolation-attestation/v1"
EXECUTION_ENVIRONMENT_ATTESTATION_SCHEMA_VERSION = (
    "supervisor-execution-environment-attestation/v1"
)
GRADE_REVISION_REF_SCHEMA_VERSION = "supervisor-grade-revision-ref/v1"
PRIMARY_REVIEW_PACKET_SCHEMA_VERSION = "supervisor-blinded-primary-review/v2"
RAW_TEST_ARTIFACT_SCHEMA_VERSION = "supervisor-raw-test-artifact/v1"

_ARM_IDENTITY_KEY_TOKENS = frozenset({"arm", "assignment", "treatment"})
_ARM_IDENTITY_EXACT_VALUES = frozenset(
    {
        "a",
        "b",
        "c",
        "arm_a",
        "arm_b",
        "arm_c",
        *(arm.value for arm in Arm),
    }
)
_UNAMBIGUOUS_ARM_IDENTITY_RE = re.compile(
    r"""
    (?<![a-z0-9])
    (?:
        production[\s_-]*baseline
        | compute[\s_-]*matched[\s_-]*direct
    )
    (?![a-z0-9])
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)
_SUPERVISOR_CONTEXT_RE = re.compile(
    r"""
    \b
    (?:ran|executed|selected|using|via|under|runtime|candidate|variant|policy)
    \b
    [^\n]{0,80}
    \bsupervisor\b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)
_EXPLICIT_ARM_IDENTITY_RE = re.compile(
    r"""
    \b
    (?:
        arm
        | treatment
        | harness[\s_-]*arm
        | experiment[\s_-]*arm
        | assignment[\s_-]*arm
    )
    (?:[\s_-]*(?:id|name|label))?
    ["']?
    (?:\s*[:=]\s*|[\s._-]+)
    ["']?
    (?:
        (?:arm[\s._-]*)?[abc]
        | production[\s_-]*baseline
        | supervisor
        | compute[\s_-]*matched[\s_-]*direct
    )
    \b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)
_SEMANTIC_OUTCOME_LEAK_RE = re.compile(
    r"""
    (?:
        \b(?:lead|lead[\s_-]*reviewer|lead[\s_-]*agent|adjudicator)\b
        [^\n.!?]{0,100}
        \b(?:accept(?:ed|s)?|reject(?:ed|s)?|approv(?:ed|es)?|fail(?:ed|s)?|
            pass(?:ed|es)?|winner|verdict|decision|outcome)\b
        |
        \b(?:accept(?:ed|s)?|reject(?:ed|s)?|approv(?:ed|es)?|fail(?:ed|s)?|
            pass(?:ed|es)?|winner|verdict|decision|outcome)\b
        [^\n.!?]{0,100}
        \b(?:by|from)\s+(?:the\s+)?(?:lead|lead[\s_-]*reviewer|
            lead[\s_-]*agent|adjudicator)\b
    )
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class ArmBudget:
    max_tokens: int
    max_cost_usd: float
    timeout_s: int
    max_retries: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens <= 0
        ):
            raise ValueError("arm max_tokens must be a positive integer")
        if (
            isinstance(self.max_cost_usd, bool)
            or not isinstance(self.max_cost_usd, (int, float))
            or not math.isfinite(float(self.max_cost_usd))
            or float(self.max_cost_usd) < 0
        ):
            raise ValueError("arm max_cost_usd must be finite and non-negative")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, int)
            or self.timeout_s <= 0
        ):
            raise ValueError("arm timeout_s must be a positive integer")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or self.max_retries < 0
        ):
            raise ValueError("arm max_retries must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
        }


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    assignment_version: str
    hmac_key: bytes = field(repr=False)
    arm_budgets: Mapping[Arm, ArmBudget]
    primary_comparison: tuple[Arm, Arm] = (Arm.B, Arm.C)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized_budgets = {
            Arm(arm): budget for arm, budget in self.arm_budgets.items()
        }
        object.__setattr__(
            self,
            "arm_budgets",
            MappingProxyType(normalized_budgets),
        )
        metadata = dict(self.metadata)
        roster = metadata.get("assignment_roster")
        if roster is not None:
            if not isinstance(roster, Sequence) or isinstance(
                roster,
                (str, bytes),
            ):
                raise ValueError(
                    "assignment_roster must be a frozen sequence"
                )
            entries = tuple(str(value).strip() for value in roster)
            if (
                not entries
                or any(not value for value in entries)
                or len(entries) != len(set(entries))
            ):
                raise ValueError(
                    "assignment_roster must contain unique non-empty identities"
                )
            metadata["assignment_roster"] = entries
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(metadata),
        )
        missing = set(Arm) - set(normalized_budgets)
        if missing:
            raise ValueError(
                "experiment missing arm budgets: "
                + ", ".join(sorted(arm.value for arm in missing))
            )
        if normalized_budgets[Arm.B] != normalized_budgets[Arm.C]:
            raise ValueError(
                "arms B and C must have identical ex-ante resource ceilings"
            )
        if not self.hmac_key:
            raise ValueError("experiment assignment requires a non-empty HMAC key")
        if self.primary_comparison != (Arm.B, Arm.C):
            raise ValueError(
                "primary comparison must be supervisor B vs compute-matched direct C"
            )


@dataclass(frozen=True)
class Assignment:
    experiment_id: str
    task_id: str
    canonical_task_id: str
    assignment_version: str
    assignment_id: str
    order: tuple[Arm, Arm, Arm]
    block: Mapping[str, str]
    assigned_at_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "canonical_task_id": self.canonical_task_id,
            "assignment_version": self.assignment_version,
            "assignment_id": self.assignment_id,
            "order": [arm.value for arm in self.order],
            "block": dict(self.block),
            "assigned_at_ms": self.assigned_at_ms,
        }


@dataclass(frozen=True)
class PrimaryReviewReceipt:
    experiment_id: str
    task_id: str
    reviewer_id: str
    primary_packet_hash: str
    review: Mapping[str, Any]
    review_hash: str
    completed_at_ms: int
    persisted_at_ms: int
    receipt_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "review",
            MappingProxyType(dict(self.review)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "reviewer_id": self.reviewer_id,
            "primary_packet_hash": self.primary_packet_hash,
            "review": dict(self.review),
            "review_hash": self.review_hash,
            "completed_at_ms": self.completed_at_ms,
            "persisted_at_ms": self.persisted_at_ms,
            "receipt_hash": self.receipt_hash,
        }


@dataclass(frozen=True)
class IsolationAttestation:
    isolation_id: str
    workspace_id: str
    session_id: str
    cache_namespace: str
    memory_namespace: str
    lesson_namespace: str
    enforced: bool
    schema_version: str = ISOLATION_ATTESTATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "isolation_id": self.isolation_id,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "cache_namespace": self.cache_namespace,
            "memory_namespace": self.memory_namespace,
            "lesson_namespace": self.lesson_namespace,
            "enforced": self.enforced,
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "IsolationAttestation":
        return cls(
            isolation_id=str(value.get("isolation_id") or ""),
            workspace_id=str(value.get("workspace_id") or ""),
            session_id=str(value.get("session_id") or ""),
            cache_namespace=str(value.get("cache_namespace") or ""),
            memory_namespace=str(value.get("memory_namespace") or ""),
            lesson_namespace=str(value.get("lesson_namespace") or ""),
            enforced=value.get("enforced"),
            schema_version=str(
                value.get("schema_version")
                or ISOLATION_ATTESTATION_SCHEMA_VERSION
            ),
        )


@dataclass(frozen=True)
class ExecutionEnvironmentAttestation:
    attestation_id: str
    mode: str
    backend: str
    image_digest: str
    architecture: str
    os_name: str
    network_policy: str
    resource_limits: Mapping[str, Any]
    enforced: bool
    schema_version: str = EXECUTION_ENVIRONMENT_ATTESTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resource_limits",
            MappingProxyType(dict(self.resource_limits)),
        )

    @property
    def operational(self) -> bool:
        return self.mode == "operational"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "attestation_id": self.attestation_id,
            "mode": self.mode,
            "backend": self.backend,
            "image_digest": self.image_digest,
            "architecture": self.architecture,
            "os_name": self.os_name,
            "network_policy": self.network_policy,
            "resource_limits": dict(self.resource_limits),
            "enforced": self.enforced,
            "operational": self.operational,
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "ExecutionEnvironmentAttestation":
        mode = str(value.get("mode") or "").strip().casefold()
        operational = value.get("operational")
        if not mode and isinstance(operational, bool):
            mode = "operational" if operational else "hermetic"
        return cls(
            attestation_id=str(value.get("attestation_id") or ""),
            mode=mode,
            backend=str(value.get("backend") or ""),
            image_digest=str(
                value.get("image_digest")
                or value.get("container_digest")
                or ""
            ),
            architecture=str(
                value.get("architecture") or value.get("arch") or ""
            ),
            os_name=str(value.get("os_name") or value.get("os") or ""),
            network_policy=str(value.get("network_policy") or ""),
            resource_limits=dict(value.get("resource_limits") or {}),
            enforced=value.get("enforced", value.get("attested")),
            schema_version=str(
                value.get("schema_version")
                or EXECUTION_ENVIRONMENT_ATTESTATION_SCHEMA_VERSION
            ),
        )


@dataclass(frozen=True)
class ArmExecutionReceipt:
    execution_id: str
    result_id: str
    assignment_id: str
    task_id: str
    canonical_task_id: str
    task_spec_hash: str
    arm: Arm
    frozen_result_hash: str
    attempts: int
    cost_usd: float
    latency_ms: int
    token_usage: Mapping[str, Any]
    attempt_records: tuple[Mapping[str, Any], ...]
    isolation: IsolationAttestation
    environment: ExecutionEnvironmentAttestation
    runtime_manifest: Mapping[str, Any]
    schema_version: str = ARM_EXECUTION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "arm", Arm(self.arm))
        object.__setattr__(
            self,
            "token_usage",
            MappingProxyType(dict(self.token_usage)),
        )
        object.__setattr__(
            self,
            "attempt_records",
            tuple(
                MappingProxyType(dict(record))
                for record in self.attempt_records
            ),
        )
        object.__setattr__(
            self,
            "runtime_manifest",
            MappingProxyType(dict(self.runtime_manifest)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "result_id": self.result_id,
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "canonical_task_id": self.canonical_task_id,
            "task_spec_hash": self.task_spec_hash,
            "arm": self.arm.value,
            "frozen_result_hash": self.frozen_result_hash,
            "attempts": self.attempts,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "token_usage": dict(self.token_usage),
            "attempt_records": [
                dict(record) for record in self.attempt_records
            ],
            "isolation": self.isolation.to_dict(),
            "environment": self.environment.to_dict(),
            "runtime_manifest": dict(self.runtime_manifest),
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "ArmExecutionReceipt":
        isolation = value.get("isolation")
        environment = value.get("environment")
        if not isinstance(isolation, MappingABC):
            raise ValueError("execution receipt isolation must be a mapping")
        if not isinstance(environment, MappingABC):
            raise ValueError("execution receipt environment must be a mapping")
        records = value.get("attempt_records") or ()
        if not isinstance(records, Sequence) or isinstance(
            records,
            (str, bytes),
        ):
            raise ValueError(
                "execution receipt attempt_records must be a sequence"
            )
        runtime_manifest = value.get("runtime_manifest")
        if not isinstance(runtime_manifest, MappingABC):
            raise ValueError(
                "execution receipt runtime_manifest must be a mapping"
            )
        return cls(
            execution_id=str(value.get("execution_id") or ""),
            result_id=str(value.get("result_id") or ""),
            assignment_id=str(value.get("assignment_id") or ""),
            task_id=str(value.get("task_id") or ""),
            canonical_task_id=str(
                value.get("canonical_task_id") or ""
            ),
            task_spec_hash=str(value.get("task_spec_hash") or ""),
            arm=Arm(str(value.get("arm") or "")),
            frozen_result_hash=str(
                value.get("frozen_result_hash") or ""
            ),
            attempts=value.get("attempts"),
            cost_usd=value.get("cost_usd"),
            latency_ms=value.get("latency_ms"),
            token_usage=dict(value.get("token_usage") or {}),
            attempt_records=tuple(dict(record) for record in records),
            isolation=IsolationAttestation.from_mapping(isolation),
            environment=ExecutionEnvironmentAttestation.from_mapping(
                environment
            ),
            runtime_manifest=dict(runtime_manifest),
            schema_version=str(
                value.get("schema_version")
                or ARM_EXECUTION_RECEIPT_SCHEMA_VERSION
            ),
        )


@dataclass(frozen=True)
class GradeRevisionRef:
    grade_id: str
    revision_hash: str
    revision_number: int
    run_id: str
    run_envelope_hash: str
    supersedes_grade_id: str | None
    schema_version: str = GRADE_REVISION_REF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not str(self.grade_id).strip():
            raise ValueError("grade revision ref grade_id is required")
        for field_name, value in (
            ("revision_hash", self.revision_hash),
            ("run_envelope_hash", self.run_envelope_hash),
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", str(value)):
                raise ValueError(
                    f"grade revision ref {field_name} must be a sha256 digest"
                )
        if self.revision_number <= 0:
            raise ValueError(
                "grade revision ref revision_number must be positive"
            )
        if not str(self.run_id).strip():
            raise ValueError("grade revision ref run_id is required")
        if self.schema_version != GRADE_REVISION_REF_SCHEMA_VERSION:
            raise ValueError("grade revision ref schema is invalid")

    @classmethod
    def from_revision(cls, revision: GradeRevision) -> "GradeRevisionRef":
        return cls(
            grade_id=revision.grade_id,
            revision_hash=revision.revision_hash,
            revision_number=revision.revision_number,
            run_id=revision.run_envelope.run_id,
            run_envelope_hash=revision.run_envelope.run_envelope_hash,
            supersedes_grade_id=revision.supersedes_grade_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "grade_id": self.grade_id,
            "revision_hash": self.revision_hash,
            "revision_number": self.revision_number,
            "run_id": self.run_id,
            "run_envelope_hash": self.run_envelope_hash,
            "supersedes_grade_id": self.supersedes_grade_id,
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "GradeRevisionRef":
        return cls(
            grade_id=str(value.get("grade_id") or ""),
            revision_hash=str(value.get("revision_hash") or ""),
            revision_number=int(value.get("revision_number") or 0),
            run_id=str(value.get("run_id") or ""),
            run_envelope_hash=str(
                value.get("run_envelope_hash") or ""
            ),
            supersedes_grade_id=(
                str(value["supersedes_grade_id"])
                if value.get("supersedes_grade_id") is not None
                else None
            ),
            schema_version=str(
                value.get("schema_version")
                or GRADE_REVISION_REF_SCHEMA_VERSION
            ),
        )


@dataclass(frozen=True)
class PrimaryReviewerPacket(MappingABC[str, Any]):
    task: Mapping[str, Any]
    diff: Mapping[str, Any]
    tests: Mapping[str, Any]
    packet_hash: str
    schema_version: str = PRIMARY_REVIEW_PACKET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "task", MappingProxyType(dict(self.task)))
        object.__setattr__(self, "diff", MappingProxyType(dict(self.diff)))
        object.__setattr__(self, "tests", MappingProxyType(dict(self.tests)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task": dict(self.task),
            "diff": dict(self.diff),
            "tests": dict(self.tests),
            "packet_hash": self.packet_hash,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


PrimaryReviewPacket = PrimaryReviewerPacket


@dataclass(frozen=True)
class ArmExecution:
    frozen_result: FrozenTaskResult
    attempts: int
    cost_usd: float
    latency_ms: int
    metadata: Mapping[str, Any] = field(default_factory=dict)
    receipt: ArmExecutionReceipt | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


class ArmExecutionError(RuntimeError):
    """Terminal arm failure carrying the usage already consumed."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        cost_usd: float,
        latency_ms: int,
        token_usage: Mapping[str, Any] | None = None,
        attempt_records: tuple[Mapping[str, Any], ...] = (),
        failure_classification: str = "treatment_execution_failure",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = max(0, int(attempts))
        self.cost_usd = max(0.0, float(cost_usd))
        self.latency_ms = max(0, int(latency_ms))
        self.token_usage = dict(token_usage or {})
        self.attempt_records = tuple(dict(item) for item in attempt_records)
        self.failure_classification = str(failure_classification)
        self.metadata = dict(metadata or {})


class CommonPreTreatmentInfrastructureError(ArmExecutionError):
    """A shared outage detected before any treatment in an A/B/C block."""

    def __init__(self, message: str, *, latency_ms: int = 0) -> None:
        super().__init__(
            message,
            attempts=0,
            cost_usd=0.0,
            latency_ms=latency_ms,
            failure_classification=(
                COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE
            ),
            metadata={
                "pre_treatment": True,
                "common_across_arms": True,
            },
        )


@dataclass(frozen=True)
class ArmOutcome:
    arm: Arm
    status: str
    grade: Grade
    attempts: int
    cost_usd: float
    latency_ms: int
    frozen_result_hash: str
    original_frozen_result_hash: str
    blinding_removed_paths: tuple[str, ...] = ()
    grade_revision: GradeRevisionRef | None = None
    execution_receipt: ArmExecutionReceipt | None = None
    token_usage: Mapping[str, Any] = field(default_factory=dict)
    attempt_records: tuple[Mapping[str, Any], ...] = ()
    failure_classification: str = ""
    error: str = ""

    @property
    def blinded_frozen_result_hash(self) -> str:
        return self.frozen_result_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm.value,
            "status": self.status,
            "grade": self.grade.to_dict(),
            "attempts": self.attempts,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "frozen_result_hash": self.frozen_result_hash,
            "original_frozen_result_hash": self.original_frozen_result_hash,
            "blinded_frozen_result_hash": self.blinded_frozen_result_hash,
            "blinding": {
                "original_frozen_result_hash": self.original_frozen_result_hash,
                "blinded_frozen_result_hash": self.blinded_frozen_result_hash,
                "removed_paths": list(self.blinding_removed_paths),
            },
            "grade_revision": (
                None
                if self.grade_revision is None
                else self.grade_revision.to_dict()
            ),
            "execution_receipt": (
                None
                if self.execution_receipt is None
                else self.execution_receipt.to_dict()
            ),
            "token_usage": dict(self.token_usage),
            "attempt_records": [
                dict(record) for record in self.attempt_records
            ],
            "failure_classification": self.failure_classification,
            "error": self.error,
        }


@dataclass(frozen=True)
class TaskExperimentResult:
    experiment_id: str
    task_id: str
    assignment: Assignment
    outcomes: tuple[ArmOutcome, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "assignment": self.assignment.to_dict(),
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


@runtime_checkable
class ArmExecutor(Protocol):
    async def execute(
        self,
        *,
        arm: Arm,
        task: TaskSpec,
        budget: ArmBudget,
        assignment_id: str,
    ) -> ArmExecution:
        ...


class SqliteExperimentStore:
    """Persist immutable assignment before any arm starts."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(Path(path).expanduser())
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiment_assignments (
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              canonical_task_id TEXT NOT NULL,
              assignment_version TEXT NOT NULL,
              assignment_id TEXT NOT NULL,
              order_json TEXT NOT NULL,
              block_json TEXT NOT NULL,
              assigned_at_ms INTEGER NOT NULL,
              PRIMARY KEY(experiment_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS experiment_task_specs (
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              canonical_task_id TEXT NOT NULL,
              task_spec_hash TEXT NOT NULL,
              task_spec_json TEXT NOT NULL,
              persisted_at_ms INTEGER NOT NULL,
              PRIMARY KEY(experiment_id, task_id),
              UNIQUE(experiment_id, canonical_task_id)
            );
            CREATE TABLE IF NOT EXISTS experiment_frozen_results (
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              arm TEXT NOT NULL,
              frozen_result_hash TEXT NOT NULL,
              frozen_result_json TEXT NOT NULL,
              persisted_at_ms INTEGER NOT NULL,
              PRIMARY KEY(experiment_id, task_id, arm),
              UNIQUE(frozen_result_hash)
            );
            CREATE TABLE IF NOT EXISTS experiment_task_results (
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              result_json TEXT NOT NULL,
              recorded_at_ms INTEGER NOT NULL,
              PRIMARY KEY(experiment_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS experiment_primary_reviews (
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              reviewer_id TEXT NOT NULL,
              primary_packet_hash TEXT NOT NULL,
              review_json TEXT NOT NULL,
              review_hash TEXT NOT NULL,
              completed_at_ms INTEGER NOT NULL,
              persisted_at_ms INTEGER NOT NULL,
              receipt_hash TEXT NOT NULL UNIQUE,
              PRIMARY KEY(experiment_id, task_id, reviewer_id)
            );
            CREATE TABLE IF NOT EXISTS experiment_transitions (
              transition_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              arm TEXT NOT NULL,
              kind TEXT NOT NULL,
              idempotency_key TEXT,
              payload_json TEXT NOT NULL,
              previous_transition_hash TEXT,
              transition_hash TEXT NOT NULL UNIQUE,
              recorded_at_ms INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS experiment_transitions_task
              ON experiment_transitions(
                experiment_id, task_id, transition_sequence
              );
            CREATE TRIGGER IF NOT EXISTS experiment_assignments_no_update
            BEFORE UPDATE ON experiment_assignments
            BEGIN
              SELECT RAISE(ABORT, 'experiment assignments are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_assignments_no_delete
            BEFORE DELETE ON experiment_assignments
            BEGIN
              SELECT RAISE(ABORT, 'experiment assignments are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_task_specs_no_update
            BEFORE UPDATE ON experiment_task_specs
            BEGIN
              SELECT RAISE(ABORT, 'experiment task specs are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_task_specs_no_delete
            BEFORE DELETE ON experiment_task_specs
            BEGIN
              SELECT RAISE(ABORT, 'experiment task specs are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_frozen_results_no_update
            BEFORE UPDATE ON experiment_frozen_results
            BEGIN
              SELECT RAISE(ABORT, 'experiment frozen results are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_frozen_results_no_delete
            BEFORE DELETE ON experiment_frozen_results
            BEGIN
              SELECT RAISE(ABORT, 'experiment frozen results are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_task_results_no_update
            BEFORE UPDATE ON experiment_task_results
            BEGIN
              SELECT RAISE(ABORT, 'experiment results are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_task_results_no_delete
            BEFORE DELETE ON experiment_task_results
            BEGIN
              SELECT RAISE(ABORT, 'experiment results are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_primary_reviews_no_update
            BEFORE UPDATE ON experiment_primary_reviews
            BEGIN
              SELECT RAISE(ABORT, 'primary review receipts are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_primary_reviews_no_delete
            BEFORE DELETE ON experiment_primary_reviews
            BEGIN
              SELECT RAISE(ABORT, 'primary review receipts are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_transitions_no_update
            BEFORE UPDATE ON experiment_transitions
            BEGIN
              SELECT RAISE(ABORT, 'experiment transitions are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS experiment_transitions_no_delete
            BEFORE DELETE ON experiment_transitions
            BEGIN
              SELECT RAISE(ABORT, 'experiment transitions are immutable');
            END;
            """
        )
        self._ensure_assignment_identity_schema()
        self._ensure_transition_idempotency_schema()

    def _ensure_assignment_identity_schema(self) -> None:
        columns = {
            str(row["name"])
            for row in self._conn.execute(
                "PRAGMA table_info(experiment_assignments)"
            )
        }
        if "canonical_task_id" not in columns:
            self._conn.execute(
                "ALTER TABLE experiment_assignments "
                "ADD COLUMN canonical_task_id TEXT"
            )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                 experiment_assignments_canonical_task
               ON experiment_assignments(
                 experiment_id, canonical_task_id
               )
               WHERE canonical_task_id IS NOT NULL
                 AND canonical_task_id <> ''"""
        )

    def _ensure_transition_idempotency_schema(self) -> None:
        columns = {
            str(row["name"])
            for row in self._conn.execute(
                "PRAGMA table_info(experiment_transitions)"
            )
        }
        if "idempotency_key" not in columns:
            self._conn.execute(
                "ALTER TABLE experiment_transitions "
                "ADD COLUMN idempotency_key TEXT"
            )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                 experiment_transitions_idempotency
               ON experiment_transitions(
                 experiment_id, task_id, idempotency_key
               )
               WHERE idempotency_key IS NOT NULL"""
        )

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        if self._conn.in_transaction:
            raise RuntimeError("nested experiment-store transaction is forbidden")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def put_task_spec(
        self,
        experiment_id: str,
        task: TaskSpec,
    ) -> TaskSpec:
        with self._write_transaction():
            return self._put_task_spec_locked(experiment_id, task)

    def _put_task_spec_locked(
        self,
        experiment_id: str,
        task: TaskSpec,
    ) -> TaskSpec:
        existing = self.get_task_spec(experiment_id, task.task_id)
        if existing is None:
            canonical_task_id = canonical_task_identity(task)
            try:
                self._conn.execute(
                    """INSERT INTO experiment_task_specs(
                         experiment_id, task_id, canonical_task_id,
                         task_spec_hash, task_spec_json, persisted_at_ms
                       ) VALUES(?, ?, ?, ?, ?, ?)""",
                    (
                        experiment_id,
                        task.task_id,
                        canonical_task_id,
                        task.spec_hash,
                        json.dumps(
                            task.to_dict(),
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        int(time.time() * 1000),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                alias = self._conn.execute(
                    """SELECT task_id FROM experiment_task_specs
                       WHERE experiment_id=? AND canonical_task_id=?""",
                    (experiment_id, canonical_task_id),
                ).fetchone()
                if (
                    alias is not None
                    and str(alias["task_id"]) != task.task_id
                ):
                    raise ValueError(
                        "canonical task identity is already persisted under "
                        "a different task_id alias"
                    ) from exc
                raise
            existing = self.get_task_spec(experiment_id, task.task_id)
        if existing is None:
            raise RuntimeError("TaskSpec persistence failed")
        if (
            existing.spec_hash != task.spec_hash
            or existing.to_dict() != task.to_dict()
        ):
            raise ValueError("persisted TaskSpec discrepancy")
        return existing

    def get_task_spec(
        self,
        experiment_id: str,
        task_id: str,
    ) -> TaskSpec | None:
        row = self._conn.execute(
            """SELECT * FROM experiment_task_specs
               WHERE experiment_id=? AND task_id=?""",
            (experiment_id, task_id),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["task_spec_json"]))
            if not isinstance(payload, MappingABC):
                raise TypeError("task_spec_json must decode to an object")
            task = TaskSpec.from_dict(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("persisted TaskSpec is invalid") from exc
        if (
            task.task_id != str(row["task_id"])
            or task.spec_hash != str(row["task_spec_hash"])
            or canonical_task_identity(task)
            != str(row["canonical_task_id"])
        ):
            raise ValueError("persisted TaskSpec identity is invalid")
        return task

    def put_frozen_result(
        self,
        *,
        experiment_id: str,
        task_id: str,
        arm: Arm,
        frozen_result: FrozenTaskResult,
    ) -> FrozenTaskResult:
        payload = json.dumps(
            frozen_result.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._write_transaction():
            existing = self._conn.execute(
                """SELECT frozen_result_json
                   FROM experiment_frozen_results
                   WHERE experiment_id=? AND task_id=? AND arm=?""",
                (experiment_id, task_id, arm.value),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    """INSERT INTO experiment_frozen_results(
                         experiment_id, task_id, arm, frozen_result_hash,
                         frozen_result_json, persisted_at_ms
                       ) VALUES(?, ?, ?, ?, ?, ?)""",
                    (
                        experiment_id,
                        task_id,
                        arm.value,
                        frozen_result.result_hash,
                        payload,
                        int(time.time() * 1000),
                    ),
                )
            elif str(existing["frozen_result_json"]) != payload:
                raise ValueError("persisted frozen result discrepancy")
        persisted = self.get_frozen_result(
            experiment_id=experiment_id,
            task_id=task_id,
            arm=arm,
        )
        if persisted is None:
            raise RuntimeError("frozen result persistence failed")
        return persisted

    def get_frozen_result(
        self,
        *,
        experiment_id: str,
        task_id: str,
        arm: Arm,
    ) -> FrozenTaskResult | None:
        row = self._conn.execute(
            """SELECT frozen_result_json, frozen_result_hash
               FROM experiment_frozen_results
               WHERE experiment_id=? AND task_id=? AND arm=?""",
            (experiment_id, task_id, arm.value),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["frozen_result_json"]))
            if not isinstance(payload, MappingABC):
                raise TypeError(
                    "frozen_result_json must decode to an object"
                )
            frozen = _frozen_result_from_dict(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("persisted frozen result is invalid") from exc
        if frozen.result_hash != str(row["frozen_result_hash"]):
            raise ValueError("persisted frozen result hash is invalid")
        return frozen

    def put_assignment(self, assignment: Assignment) -> Assignment:
        with self._write_transaction():
            return self._put_assignment_locked(assignment)

    def _put_assignment_locked(
        self,
        assignment: Assignment,
    ) -> Assignment:
        existing = self.get_assignment(
            assignment.experiment_id,
            assignment.task_id,
        )
        if existing is None:
            self._conn.execute(
                """INSERT INTO experiment_assignments(
                     experiment_id, task_id, canonical_task_id,
                     assignment_version,
                     assignment_id, order_json, block_json, assigned_at_ms
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assignment.experiment_id,
                    assignment.task_id,
                    assignment.canonical_task_id,
                    assignment.assignment_version,
                    assignment.assignment_id,
                    json.dumps(
                        [arm.value for arm in assignment.order],
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        dict(assignment.block),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    assignment.assigned_at_ms,
                ),
            )
            existing = self.get_assignment(
                assignment.experiment_id,
                assignment.task_id,
            )
        if existing is None:
            raise RuntimeError("assignment persistence failed")
        _assert_assignment_write_matches(existing, assignment)
        return existing

    def count_assignments_in_stratum(
        self,
        experiment_id: str,
        stratum_id: str,
    ) -> int:
        count = 0
        rows = self._conn.execute(
            """SELECT block_json FROM experiment_assignments
               WHERE experiment_id=?""",
            (experiment_id,),
        ).fetchall()
        for row in rows:
            try:
                block = json.loads(row["block_json"])
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "persisted assignment block is invalid"
                ) from exc
            if (
                isinstance(block, MappingABC)
                and block.get("stratum_id") == stratum_id
            ):
                count += 1
        return count

    def get_assignment(
        self,
        experiment_id: str,
        task_id: str,
    ) -> Assignment | None:
        row = self._conn.execute(
            """SELECT * FROM experiment_assignments
               WHERE experiment_id=? AND task_id=?""",
            (experiment_id, task_id),
        ).fetchone()
        if row is None:
            return None
        try:
            order_payload = json.loads(row["order_json"])
            block_payload = json.loads(row["block_json"])
            if not isinstance(order_payload, list):
                raise TypeError("order_json must decode to a list")
            if not isinstance(block_payload, dict):
                raise TypeError("block_json must decode to an object")
            order = tuple(Arm(value) for value in order_payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("persisted assignment encoding is invalid") from exc
        return Assignment(
            experiment_id=row["experiment_id"],
            task_id=row["task_id"],
            canonical_task_id=(
                str(row["canonical_task_id"])
                if row["canonical_task_id"]
                else str(block_payload.get("canonical_task_id") or row["task_id"])
            ),
            assignment_version=row["assignment_version"],
            assignment_id=row["assignment_id"],
            order=order,
            block=block_payload,
            assigned_at_ms=int(row["assigned_at_ms"]),
        )

    def get_assignment_by_canonical_task_id(
        self,
        experiment_id: str,
        canonical_task_id: str,
    ) -> Assignment | None:
        row = self._conn.execute(
            """SELECT task_id FROM experiment_assignments
               WHERE experiment_id=? AND canonical_task_id=?""",
            (experiment_id, canonical_task_id),
        ).fetchone()
        if row is None:
            return None
        return self.get_assignment(experiment_id, str(row["task_id"]))

    def record_primary_review(
        self,
        *,
        experiment_id: str,
        task_id: str,
        reviewer_id: str,
        primary_packet_hash: str,
        review: Mapping[str, Any],
        completed_at_ms: int,
    ) -> PrimaryReviewReceipt:
        for field_name, value in (
            ("experiment_id", experiment_id),
            ("task_id", task_id),
            ("reviewer_id", reviewer_id),
        ):
            if not str(value).strip():
                raise ValueError(f"{field_name} must be non-empty")
        if not re.fullmatch(r"[0-9a-f]{64}", primary_packet_hash):
            raise ValueError("primary_packet_hash must be a sha256 digest")
        if (
            isinstance(completed_at_ms, bool)
            or not isinstance(completed_at_ms, int)
            or completed_at_ms <= 0
        ):
            raise ValueError("completed_at_ms must be a positive integer")
        review_payload = dict(review)
        review_hash = _sha256_json(review_payload)
        with self._write_transaction():
            existing = self._get_primary_review_receipt(
                experiment_id,
                task_id,
                reviewer_id,
            )
            if existing is not None:
                if (
                    existing.primary_packet_hash == primary_packet_hash
                    and existing.review_hash == review_hash
                    and existing.completed_at_ms == completed_at_ms
                ):
                    return existing
                raise ValueError("primary review receipt discrepancy")
            persisted_at_ms = int(time.time() * 1000)
            if completed_at_ms > persisted_at_ms:
                raise ValueError(
                    "primary review completion cannot be after persistence"
                )
            body = {
                "experiment_id": str(experiment_id),
                "task_id": str(task_id),
                "reviewer_id": str(reviewer_id),
                "primary_packet_hash": primary_packet_hash,
                "review_hash": review_hash,
                "completed_at_ms": completed_at_ms,
                "persisted_at_ms": persisted_at_ms,
            }
            receipt_hash = _sha256_json(body)
            self._conn.execute(
                """INSERT INTO experiment_primary_reviews(
                     experiment_id, task_id, reviewer_id,
                     primary_packet_hash, review_json, review_hash,
                     completed_at_ms, persisted_at_ms, receipt_hash
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    experiment_id,
                    task_id,
                    reviewer_id,
                    primary_packet_hash,
                    json.dumps(
                        review_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    review_hash,
                    completed_at_ms,
                    persisted_at_ms,
                    receipt_hash,
                ),
            )
            receipt = self._get_primary_review_receipt(
                experiment_id,
                task_id,
                reviewer_id,
            )
            if receipt is None:
                raise RuntimeError("primary review persistence failed")
            return receipt

    def get_primary_review_receipts(
        self,
        experiment_id: str,
        task_id: str,
    ) -> tuple[PrimaryReviewReceipt, ...]:
        rows = self._conn.execute(
            """SELECT reviewer_id FROM experiment_primary_reviews
               WHERE experiment_id=? AND task_id=?
               ORDER BY persisted_at_ms, reviewer_id""",
            (experiment_id, task_id),
        ).fetchall()
        receipts = [
            self._get_primary_review_receipt(
                experiment_id,
                task_id,
                str(row["reviewer_id"]),
            )
            for row in rows
        ]
        return tuple(
            receipt for receipt in receipts if receipt is not None
        )

    def _get_primary_review_receipt(
        self,
        experiment_id: str,
        task_id: str,
        reviewer_id: str,
    ) -> PrimaryReviewReceipt | None:
        row = self._conn.execute(
            """SELECT * FROM experiment_primary_reviews
               WHERE experiment_id=? AND task_id=? AND reviewer_id=?""",
            (experiment_id, task_id, reviewer_id),
        ).fetchone()
        if row is None:
            return None
        try:
            review = json.loads(row["review_json"])
        except json.JSONDecodeError as exc:
            raise ValueError(
                "persisted primary review is invalid"
            ) from exc
        if not isinstance(review, MappingABC):
            raise ValueError("persisted primary review must be a mapping")
        receipt = PrimaryReviewReceipt(
            experiment_id=str(row["experiment_id"]),
            task_id=str(row["task_id"]),
            reviewer_id=str(row["reviewer_id"]),
            primary_packet_hash=str(row["primary_packet_hash"]),
            review=dict(review),
            review_hash=str(row["review_hash"]),
            completed_at_ms=int(row["completed_at_ms"]),
            persisted_at_ms=int(row["persisted_at_ms"]),
            receipt_hash=str(row["receipt_hash"]),
        )
        if receipt.review_hash != _sha256_json(dict(receipt.review)):
            raise ValueError("persisted primary review hash is invalid")
        receipt_body = {
            key: value
            for key, value in receipt.to_dict().items()
            if key not in {"review", "receipt_hash"}
        }
        if receipt.receipt_hash != _sha256_json(receipt_body):
            raise ValueError("persisted primary review receipt hash is invalid")
        return receipt

    def put_result(self, result: TaskExperimentResult) -> None:
        with self._write_transaction():
            self._put_result_locked(result)

    def _put_result_locked(self, result: TaskExperimentResult) -> None:
        payload = json.dumps(
            result.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        existing = self._conn.execute(
            """SELECT result_json FROM experiment_task_results
               WHERE experiment_id=? AND task_id=?""",
            (result.experiment_id, result.task_id),
        ).fetchone()
        if existing is not None:
            if str(existing["result_json"]) == payload:
                return
            raise ValueError("experiment result discrepancy")
        self._conn.execute(
            """INSERT INTO experiment_task_results(
                 experiment_id, task_id, result_json, recorded_at_ms
               ) VALUES(?, ?, ?, ?)""",
            (
                result.experiment_id,
                result.task_id,
                payload,
                int(time.time() * 1000),
            ),
        )

    def get_result(
        self,
        experiment_id: str,
        task_id: str,
    ) -> TaskExperimentResult | None:
        row = self._conn.execute(
            """SELECT result_json FROM experiment_task_results
               WHERE experiment_id=? AND task_id=?""",
            (experiment_id, task_id),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["result_json"])
            result = _task_experiment_result_from_dict(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("persisted experiment result is invalid") from exc
        if (
            result.experiment_id != experiment_id
            or result.task_id != task_id
        ):
            raise ValueError("persisted experiment result identity is invalid")
        return result

    def append_transition(
        self,
        *,
        experiment_id: str,
        task_id: str,
        kind: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        arm: Arm | None = None,
    ) -> Mapping[str, Any]:
        if not str(kind).strip():
            raise ValueError("experiment transition kind must be non-empty")
        if not str(idempotency_key).strip():
            raise ValueError(
                "experiment transition idempotency_key must be non-empty"
            )
        with self._write_transaction():
            return self._append_transition_locked(
                experiment_id=experiment_id,
                task_id=task_id,
                kind=kind,
                payload=payload,
                idempotency_key=idempotency_key,
                arm=arm,
            )

    def _append_transition_locked(
        self,
        *,
        experiment_id: str,
        task_id: str,
        kind: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        arm: Arm | None,
    ) -> Mapping[str, Any]:
        arm_value = "" if arm is None else arm.value
        payload_dict = dict(payload)
        existing = self._conn.execute(
            """SELECT * FROM experiment_transitions
               WHERE experiment_id=? AND task_id=? AND idempotency_key=?""",
            (experiment_id, task_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            transition = _transition_from_row(existing)
            if (
                transition["arm"] == arm_value
                and transition["kind"] == str(kind)
                and transition["payload"] == payload_dict
            ):
                return transition
            raise ValueError(
                "experiment transition discrepancy for idempotency key "
                f"{idempotency_key}"
            )
        previous = self._conn.execute(
            """SELECT transition_hash FROM experiment_transitions
               WHERE experiment_id=? AND task_id=?
               ORDER BY transition_sequence DESC LIMIT 1""",
            (experiment_id, task_id),
        ).fetchone()
        previous_hash = (
            str(previous["transition_hash"])
            if previous is not None
            else None
        )
        recorded_at_ms = int(time.time() * 1000)
        body = {
            "experiment_id": experiment_id,
            "task_id": task_id,
            "arm": arm_value,
            "kind": str(kind),
            "idempotency_key": idempotency_key,
            "payload": payload_dict,
            "previous_transition_hash": previous_hash,
            "recorded_at_ms": recorded_at_ms,
        }
        transition_hash = _sha256_json(body)
        payload_json = json.dumps(
            payload_dict,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        cursor = self._conn.execute(
            """INSERT INTO experiment_transitions(
                 experiment_id, task_id, arm, kind, idempotency_key,
                 payload_json, previous_transition_hash, transition_hash,
                 recorded_at_ms
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                experiment_id,
                task_id,
                body["arm"],
                kind,
                idempotency_key,
                payload_json,
                previous_hash,
                transition_hash,
                recorded_at_ms,
            ),
        )
        return {
            **body,
            "transition_sequence": int(cursor.lastrowid),
            "transition_hash": transition_hash,
        }

    def get_transitions(
        self,
        experiment_id: str,
        task_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        rows = self._conn.execute(
            """SELECT * FROM experiment_transitions
               WHERE experiment_id=? AND task_id=?
               ORDER BY transition_sequence""",
            (experiment_id, task_id),
        ).fetchall()
        transitions: list[Mapping[str, Any]] = []
        expected_previous_hash: str | None = None
        for row in rows:
            transition = _transition_from_row(row)
            if transition["previous_transition_hash"] != expected_previous_hash:
                raise ValueError("persisted experiment transition chain is invalid")
            transitions.append(transition)
            expected_previous_hash = transition["transition_hash"]
        return tuple(transitions)

    def complete_task(
        self,
        result: TaskExperimentResult,
        *,
        kind: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        """Atomically persist one terminal task result and its transition."""
        if not str(kind).strip():
            raise ValueError("experiment transition kind must be non-empty")
        if not str(idempotency_key).strip():
            raise ValueError(
                "experiment transition idempotency_key must be non-empty"
            )
        with self._write_transaction():
            self._put_result_locked(result)
            return self._append_transition_locked(
                experiment_id=result.experiment_id,
                task_id=result.task_id,
                kind=kind,
                payload=payload,
                idempotency_key=idempotency_key,
                arm=None,
            )


def _assert_assignment_write_matches(
    existing: Assignment,
    requested: Assignment,
) -> None:
    if (
        existing.experiment_id != requested.experiment_id
        or existing.task_id != requested.task_id
        or existing.canonical_task_id != requested.canonical_task_id
        or existing.assignment_version != requested.assignment_version
        or existing.assignment_id != requested.assignment_id
        or existing.order != requested.order
        or dict(existing.block) != dict(requested.block)
    ):
        raise ValueError("experiment assignment discrepancy")


def _transition_from_row(row: sqlite3.Row) -> Mapping[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError as exc:
        raise ValueError("persisted experiment transition is invalid") from exc
    idempotency_key = (
        str(row["idempotency_key"])
        if "idempotency_key" in row.keys()
        and row["idempotency_key"] is not None
        else ""
    )
    transition = {
        "transition_sequence": int(row["transition_sequence"]),
        "experiment_id": str(row["experiment_id"]),
        "task_id": str(row["task_id"]),
        "arm": str(row["arm"]),
        "kind": str(row["kind"]),
        "idempotency_key": idempotency_key,
        "payload": payload,
        "previous_transition_hash": row["previous_transition_hash"],
        "transition_hash": str(row["transition_hash"]),
        "recorded_at_ms": int(row["recorded_at_ms"]),
    }
    hash_body = {
        "experiment_id": transition["experiment_id"],
        "task_id": transition["task_id"],
        "arm": transition["arm"],
        "kind": transition["kind"],
        "payload": transition["payload"],
        "previous_transition_hash": transition["previous_transition_hash"],
        "recorded_at_ms": transition["recorded_at_ms"],
    }
    if idempotency_key:
        hash_body["idempotency_key"] = idempotency_key
    expected_hash = _sha256_json(hash_body)
    if not hmac.compare_digest(transition["transition_hash"], expected_hash):
        raise ValueError("persisted experiment transition hash is invalid")
    return transition


class ExperimentKernel:
    def __init__(
        self,
        *,
        store: SqliteExperimentStore,
        executor: ArmExecutor,
        gradebook: GradeBook | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.gradebook = gradebook or GradeBook(store.path)

    def assign(self, experiment: ExperimentSpec, task: TaskSpec) -> Assignment:
        with self.store._write_transaction():
            task = self.store._put_task_spec_locked(
                experiment.experiment_id,
                task,
            )
            canonical_task_id = canonical_task_identity(task)
            stratum = _assignment_stratum(experiment, task)
            canonical_existing = (
                self.store.get_assignment_by_canonical_task_id(
                    experiment.experiment_id,
                    canonical_task_id,
                )
            )
            if (
                canonical_existing is not None
                and canonical_existing.task_id != task.task_id
            ):
                raise ValueError(
                    "canonical task identity is already assigned under a "
                    "different task_id alias"
                )
            existing = self.store.get_assignment(
                experiment.experiment_id,
                task.task_id,
            )
            if existing is not None:
                try:
                    stratum_position = int(
                        existing.block["stratum_position"]
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "persisted assignment lacks a valid stratum position"
                    ) from exc
                block, digest, order = _derive_assignment(
                    experiment,
                    task,
                    stratum=stratum,
                    stratum_position=stratum_position,
                )
                _validate_persisted_assignment(
                    existing,
                    experiment=experiment,
                    task=task,
                    expected_block=block,
                    expected_digest=digest,
                    expected_order=order,
                )
                return existing

            stratum_position = _deterministic_stratum_position(
                experiment,
                task,
                stratum=stratum,
            )
            block, digest, order = _derive_assignment(
                experiment,
                task,
                stratum=stratum,
                stratum_position=stratum_position,
            )
            assignment = Assignment(
                experiment_id=experiment.experiment_id,
                task_id=task.task_id,
                canonical_task_id=canonical_task_id,
                assignment_version=experiment.assignment_version,
                assignment_id=digest,
                order=order,
                block=block,
                assigned_at_ms=int(time.time() * 1000),
            )
            persisted = self.store._put_assignment_locked(assignment)
            _validate_persisted_assignment(
                persisted,
                experiment=experiment,
                task=task,
                expected_block=block,
                expected_digest=digest,
                expected_order=order,
            )
            return persisted

    async def run_task(
        self,
        experiment: ExperimentSpec,
        task: TaskSpec,
        verifier: VerifierAdapter,
    ) -> TaskExperimentResult:
        assignment = self.assign(experiment, task)
        persisted_task = self.store.get_task_spec(
            experiment.experiment_id,
            task.task_id,
        )
        if persisted_task is None:
            raise RuntimeError("persisted TaskSpec is unavailable")
        task = persisted_task
        _validate_task_verifier_pin(task)
        verifier_version = _validate_verifier_adapter(verifier, task=task)
        execution_mode = _experiment_execution_mode(experiment)
        existing = self.store.get_result(experiment.experiment_id, task.task_id)
        if existing is not None:
            if existing.assignment != assignment:
                raise ValueError(
                    "persisted experiment result assignment discrepancy"
                )
            return existing
        observed_execution_ids: set[str] = set()
        observed_result_ids: set[str] = set()
        observed_isolation_ids: set[str] = set()
        observed_workspace_ids: set[str] = set()
        observed_session_ids: set[str] = set()
        observed_cache_namespaces: set[str] = set()
        observed_memory_namespaces: set[str] = set()
        observed_lesson_namespaces: set[str] = set()
        observed_environment_attestation_ids: set[str] = set()
        self.store.append_transition(
            experiment_id=experiment.experiment_id,
            task_id=task.task_id,
            kind="task.started",
            idempotency_key="task.started",
            payload={
                "assignment_id": assignment.assignment_id,
                "order": [arm.value for arm in assignment.order],
                "max_common_infrastructure_block_reruns": 1,
            },
        )
        final_outcomes: tuple[ArmOutcome, ...] | None = None
        for block_attempt in range(2):
            self.store.append_transition(
                experiment_id=experiment.experiment_id,
                task_id=task.task_id,
                kind="block.started",
                idempotency_key=f"block.{block_attempt}.started",
                payload={
                    "assignment_id": assignment.assignment_id,
                    "block_attempt": block_attempt,
                    "order": [arm.value for arm in assignment.order],
                },
            )
            outcomes: list[ArmOutcome] = []
            common_failure: Exception | None = None
            common_failure_arm: Arm | None = None
            for arm_index, arm in enumerate(assignment.order):
                transition_prefix = (
                    f"block.{block_attempt}.arm.{arm.value}"
                )
                self.store.append_transition(
                    experiment_id=experiment.experiment_id,
                    task_id=task.task_id,
                    arm=arm,
                    kind="arm.started",
                    idempotency_key=f"{transition_prefix}.started",
                    payload={
                        "assignment_id": assignment.assignment_id,
                        "block_attempt": block_attempt,
                        "budget": experiment.arm_budgets[arm].to_dict(),
                    },
                )
                try:
                    execution = await self.executor.execute(
                        arm=arm,
                        task=task,
                        budget=experiment.arm_budgets[arm],
                        assignment_id=assignment.assignment_id,
                    )
                except Exception as exc:
                    common_pre_treatment_failure = (
                        _is_common_pre_treatment_failure(exc)
                    )
                    if (
                        arm_index == 0
                        and not outcomes
                        and common_pre_treatment_failure
                    ):
                        common_failure = exc
                        common_failure_arm = arm
                        self.store.append_transition(
                            experiment_id=experiment.experiment_id,
                            task_id=task.task_id,
                            arm=arm,
                            kind="arm.common_infrastructure_failed",
                            idempotency_key=(
                                f"{transition_prefix}."
                                "common_infrastructure_failed"
                            ),
                            payload={
                                "block_attempt": block_attempt,
                                "failure_classification": (
                                    COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE
                                ),
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        break
                    if common_pre_treatment_failure:
                        exc = RuntimeError(
                            "common pre-treatment infrastructure failure "
                            "was reported after block treatment began"
                        )
                    outcome = _intention_to_treat_failure(
                        arm=arm,
                        task=task,
                        verifier_version=verifier_version,
                        assignment=assignment,
                        exc=exc,
                    )
                    outcomes.append(
                        self._with_grade_revision(
                            experiment=experiment,
                            task=task,
                            assignment=assignment,
                            outcome=outcome,
                        )
                    )
                    self.store.append_transition(
                        experiment_id=experiment.experiment_id,
                        task_id=task.task_id,
                        arm=arm,
                        kind="arm.failed",
                        idempotency_key=f"{transition_prefix}.failed",
                        payload=outcomes[-1].to_dict(),
                    )
                    continue

                receipt = _extract_execution_receipt(
                    execution,
                    allow_fixture_annotation_rebind=(
                        execution_mode == "hermetic"
                        and str(
                            task.metadata.get("tracer_mode") or ""
                        ).strip().casefold()
                        == "hermetic"
                    ),
                )
                _validate_arm_execution_receipt(
                    execution,
                    receipt=receipt,
                    arm=arm,
                    task=task,
                    assignment=assignment,
                    budget=experiment.arm_budgets[arm],
                    expected_mode=execution_mode,
                    observed_execution_ids=observed_execution_ids,
                    observed_result_ids=observed_result_ids,
                    observed_isolation_ids=observed_isolation_ids,
                    observed_workspace_ids=observed_workspace_ids,
                    observed_session_ids=observed_session_ids,
                    observed_cache_namespaces=(
                        observed_cache_namespaces
                    ),
                    observed_memory_namespaces=(
                        observed_memory_namespaces
                    ),
                    observed_lesson_namespaces=(
                        observed_lesson_namespaces
                    ),
                    observed_environment_attestation_ids=(
                        observed_environment_attestation_ids
                    ),
                )
                _validate_frozen_result_task_binding(
                    execution.frozen_result,
                    task,
                )
                verifier_bound = bind_frozen_result_to_task(
                    task,
                    execution.frozen_result,
                )
                blinded, removed_paths = _blind_frozen_result_with_audit(
                    verifier_bound
                )
                blinded = self.store.put_frozen_result(
                    experiment_id=experiment.experiment_id,
                    task_id=task.task_id,
                    arm=arm,
                    frozen_result=blinded,
                )
                try:
                    grade = await verifier.verify(blinded)
                except Exception as exc:
                    outcome = _verification_failure(
                        arm=arm,
                        task=task,
                        verifier_version=verifier_version,
                        execution=execution,
                        receipt=receipt,
                        blinded_result=blinded,
                        removed_paths=removed_paths,
                        exc=exc,
                    )
                    outcomes.append(
                        self._with_grade_revision(
                            experiment=experiment,
                            task=task,
                            assignment=assignment,
                            outcome=outcome,
                        )
                    )
                    self.store.append_transition(
                        experiment_id=experiment.experiment_id,
                        task_id=task.task_id,
                        arm=arm,
                        kind="arm.failed",
                        idempotency_key=f"{transition_prefix}.failed",
                        payload=outcomes[-1].to_dict(),
                    )
                    continue
                _validate_grade_binding(
                    grade,
                    blinded_result=blinded,
                    task=task,
                )
                outcome = ArmOutcome(
                        arm=arm,
                        status="completed",
                        grade=grade,
                        attempts=receipt.attempts,
                        cost_usd=receipt.cost_usd,
                        latency_ms=receipt.latency_ms,
                        frozen_result_hash=blinded.result_hash,
                        original_frozen_result_hash=(
                            execution.frozen_result.result_hash
                        ),
                        blinding_removed_paths=removed_paths,
                        execution_receipt=receipt,
                        token_usage=dict(receipt.token_usage),
                        attempt_records=tuple(receipt.attempt_records),
                    )
                outcomes.append(
                    self._with_grade_revision(
                        experiment=experiment,
                        task=task,
                        assignment=assignment,
                        outcome=outcome,
                    )
                )
                self.store.append_transition(
                    experiment_id=experiment.experiment_id,
                    task_id=task.task_id,
                    arm=arm,
                    kind="arm.completed",
                    idempotency_key=f"{transition_prefix}.completed",
                    payload=outcomes[-1].to_dict(),
                )

            if common_failure is not None:
                if block_attempt == 0:
                    self.store.append_transition(
                        experiment_id=experiment.experiment_id,
                        task_id=task.task_id,
                        kind="block.rerun_scheduled",
                        idempotency_key="block.0.rerun_scheduled",
                        payload={
                            "failed_arm": (
                                "" if common_failure_arm is None
                                else common_failure_arm.value
                            ),
                            "from_block_attempt": 0,
                            "to_block_attempt": 1,
                            "failure_classification": (
                                COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE
                            ),
                        },
                    )
                    continue
                final_outcomes = tuple(
                    self._with_grade_revision(
                        experiment=experiment,
                        task=task,
                        assignment=assignment,
                        outcome=_common_pre_treatment_block_failure(
                            arm=arm,
                            task=task,
                            verifier_version=verifier_version,
                            assignment=assignment,
                            exc=common_failure,
                        ),
                    )
                    for arm in assignment.order
                )
                for outcome in final_outcomes:
                    self.store.append_transition(
                        experiment_id=experiment.experiment_id,
                        task_id=task.task_id,
                        arm=outcome.arm,
                        kind="arm.failed",
                        idempotency_key=(
                            f"block.{block_attempt}.arm."
                            f"{outcome.arm.value}.failed"
                        ),
                        payload=outcome.to_dict(),
                    )
                self.store.append_transition(
                    experiment_id=experiment.experiment_id,
                    task_id=task.task_id,
                    kind="block.failed",
                    idempotency_key=f"block.{block_attempt}.failed",
                    payload={
                        "block_attempt": block_attempt,
                        "failure_classification": (
                            COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE
                        ),
                    },
                )
                break

            final_outcomes = tuple(outcomes)
            self.store.append_transition(
                experiment_id=experiment.experiment_id,
                task_id=task.task_id,
                kind="block.completed",
                idempotency_key=f"block.{block_attempt}.completed",
                payload={
                    "block_attempt": block_attempt,
                    "outcome_count": len(final_outcomes),
                    "failure_count": sum(
                        outcome.status != "completed"
                        for outcome in final_outcomes
                    ),
                },
            )
            break

        if final_outcomes is None:
            raise RuntimeError(
                "experiment block execution ended without terminal outcomes"
            )
        if (
            len(final_outcomes) != len(Arm)
            or {outcome.arm for outcome in final_outcomes} != set(Arm)
        ):
            raise RuntimeError(
                "experiment block must produce exactly one outcome per arm"
            )
        result = TaskExperimentResult(
            experiment_id=experiment.experiment_id,
            task_id=task.task_id,
            assignment=assignment,
            outcomes=final_outcomes,
        )
        failure_count = sum(
            outcome.status != "completed" for outcome in result.outcomes
        )
        self.store.complete_task(
            result,
            kind="task.failed" if failure_count else "task.completed",
            idempotency_key="task.terminal",
            payload={
                "result_hash": _sha256_json(result.to_dict()),
                "outcome_count": len(result.outcomes),
                "failure_count": failure_count,
            },
        )
        return result

    def _with_grade_revision(
        self,
        *,
        experiment: ExperimentSpec,
        task: TaskSpec,
        assignment: Assignment,
        outcome: ArmOutcome,
    ) -> ArmOutcome:
        if outcome.grade_revision is not None:
            raise ValueError("outcome already carries grade lineage")
        receipt = outcome.execution_receipt
        run_id = (
            receipt.result_id
            if receipt is not None
            else "failure-" + _sha256_json({
                "experiment_id": experiment.experiment_id,
                "assignment_id": assignment.assignment_id,
                "task_id": task.task_id,
                "arm": outcome.arm.value,
                "original_frozen_result_hash": (
                    outcome.original_frozen_result_hash
                ),
                "failure_classification": (
                    outcome.failure_classification
                ),
            })
        )
        envelope_payload = {
            "schema_version": "supervisor-experiment-run-envelope/v1",
            "experiment_id": experiment.experiment_id,
            "assignment_id": assignment.assignment_id,
            "task_id": task.task_id,
            "canonical_task_id": assignment.canonical_task_id,
            "task_spec_hash": task.spec_hash,
            "arm": outcome.arm.value,
            "original_frozen_result_hash": (
                outcome.original_frozen_result_hash
            ),
            "blinded_frozen_result_hash": outcome.frozen_result_hash,
            "execution_receipt": (
                None if receipt is None else receipt.to_dict()
            ),
        }
        run = RunEnvelopeRef(
            run_id=run_id,
            run_envelope_hash=_sha256_json(envelope_payload),
            frozen_result_hash=outcome.frozen_result_hash,
        )
        revision = self.gradebook.append_grade(
            run=run,
            grade=outcome.grade,
            verifier_config_hash=_verifier_config_hash(
                task,
                verifier_version=outcome.grade.verifier_version,
            ),
        )
        return replace(
            outcome,
            grade_revision=GradeRevisionRef.from_revision(revision),
        )

    async def regrade_arm(
        self,
        *,
        experiment_id: str,
        task_id: str,
        arm: Arm,
        verifier: VerifierAdapter,
        reason: str,
    ) -> GradeRevision:
        """Explicitly regrade a persisted frozen result without re-execution."""
        task = self.store.get_task_spec(experiment_id, task_id)
        if task is None:
            raise ValueError("cannot regrade without a persisted TaskSpec")
        result = self.store.get_result(experiment_id, task_id)
        if result is None:
            raise ValueError("cannot regrade before task execution completes")
        outcome = next(
            (candidate for candidate in result.outcomes if candidate.arm == arm),
            None,
        )
        if outcome is None or outcome.grade_revision is None:
            raise ValueError("persisted outcome lacks immutable grade lineage")
        frozen = self.store.get_frozen_result(
            experiment_id=experiment_id,
            task_id=task_id,
            arm=arm,
        )
        if frozen is None:
            raise ValueError(
                "only outcomes with a persisted frozen result can be regraded"
            )
        _validate_task_verifier_pin(task)
        _validate_verifier_adapter(verifier, task=task)
        grade = await verifier.verify(frozen)
        _validate_grade_binding(
            grade,
            blinded_result=frozen,
            task=task,
        )
        run = RunEnvelopeRef(
            run_id=outcome.grade_revision.run_id,
            run_envelope_hash=outcome.grade_revision.run_envelope_hash,
            frozen_result_hash=outcome.frozen_result_hash,
        )
        history = self.gradebook.list_revisions(run)
        if not history:
            raise ValueError("persisted outcome grade lineage is unavailable")
        revision = self.gradebook.regrade(
            run=run,
            grade=grade,
            verifier_config_hash=_verifier_config_hash(
                task,
                verifier_version=grade.verifier_version,
            ),
            supersedes_grade_id=history[-1].grade_id,
            reason=reason,
        )
        self.store.append_transition(
            experiment_id=experiment_id,
            task_id=task_id,
            arm=arm,
            kind="arm.regraded",
            idempotency_key=f"arm.{arm.value}.regrade.{revision.grade_id}",
            payload={
                "grade_id": revision.grade_id,
                "revision_hash": revision.revision_hash,
                "revision_number": revision.revision_number,
                "supersedes_grade_id": revision.supersedes_grade_id,
            },
        )
        return revision


def blind_frozen_result(result: FrozenTaskResult) -> FrozenTaskResult:
    blinded, _ = _blind_frozen_result_with_audit(result)
    return blinded


def _blind_frozen_result_with_audit(
    result: FrozenTaskResult,
) -> tuple[FrozenTaskResult, tuple[str, ...]]:
    removed_paths: list[str] = []
    metadata = _scrub_arm_identity(
        result.metadata,
        path="metadata",
        removed_paths=removed_paths,
    )
    _reject_arm_identity_scalar(result.run_result_hash, path="run_result_hash")
    _reject_arm_identity_scalar(result.patch, path="patch")
    _reject_arm_identity_scalar(result.output, path="output")
    blinded = FrozenTaskResult.create(
        task_id=result.task_id,
        task_family=result.task_family,
        task_spec_hash=result.task_spec_hash,
        run_result_hash=result.run_result_hash,
        patch=result.patch,
        output=result.output,
        metadata=metadata,
        frozen_at_ms=result.frozen_at_ms,
    )
    return blinded, tuple(removed_paths)


def build_primary_reviewer_packet(
    *,
    task: TaskSpec | Mapping[str, Any],
    diff: str | None = None,
    tests: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> PrimaryReviewerPacket:
    if evidence is not None:
        if diff is not None or tests is not None:
            raise ValueError(
                "provide raw diff/tests or legacy evidence, not both"
            )
        if set(evidence) != {"diff", "tests"}:
            raise ValueError(
                "primary review requires raw task, diff, and test artifacts; "
                "free-text summaries are not blinded proof"
            )
        diff = evidence.get("diff")
        raw_tests = evidence.get("tests")
        tests = raw_tests if isinstance(raw_tests, MappingABC) else None
    if not isinstance(diff, str) or not diff.strip():
        raise ValueError("primary review diff artifact must be non-empty text")
    if not isinstance(tests, MappingABC) or not tests:
        raise ValueError("primary review test artifact must be a non-empty mapping")
    task_artifact = _primary_task_artifact(task)
    test_receipt = _primary_test_artifact(tests)
    _reject_lead_outcome_fields(task_artifact, path="task")
    _reject_lead_outcome_fields(diff, path="diff")
    _reject_lead_outcome_fields(test_receipt, path="tests")
    blinded_task = _scrub_arm_identity(
        task_artifact,
        path="task",
        removed_paths=[],
    )
    blinded_diff = _scrub_arm_identity(
        diff,
        path="diff",
        removed_paths=[],
    )
    blinded_tests = _scrub_arm_identity(
        test_receipt,
        path="tests",
        removed_paths=[],
    )
    diff_artifact = {
        "patch": str(blinded_diff),
        "sha256": hashlib.sha256(
            str(blinded_diff).encode("utf-8")
        ).hexdigest(),
    }
    test_receipt = dict(blinded_tests)
    test_artifact = {
        "receipt": test_receipt,
        "sha256": _sha256_json(test_receipt),
    }
    body = {
        "schema_version": PRIMARY_REVIEW_PACKET_SCHEMA_VERSION,
        "task": dict(blinded_task),
        "diff": diff_artifact,
        "tests": test_artifact,
    }
    return PrimaryReviewerPacket(
        task=body["task"],
        diff=body["diff"],
        tests=body["tests"],
        packet_hash=_sha256_json(body),
    )


def validate_primary_reviewer_packet(
    packet: PrimaryReviewerPacket | Mapping[str, Any],
) -> PrimaryReviewerPacket:
    if isinstance(packet, PrimaryReviewerPacket):
        candidate = packet
    else:
        task = packet.get("task")
        diff = packet.get("diff")
        tests = packet.get("tests")
        if not all(
            isinstance(value, MappingABC)
            for value in (task, diff, tests)
        ):
            raise ValueError("primary reviewer packet artifacts are malformed")
        candidate = PrimaryReviewerPacket(
            task=dict(task),
            diff=dict(diff),
            tests=dict(tests),
            packet_hash=str(packet.get("packet_hash") or ""),
            schema_version=str(packet.get("schema_version") or ""),
        )
    if candidate.schema_version != PRIMARY_REVIEW_PACKET_SCHEMA_VERSION:
        raise ValueError("primary reviewer packet schema is invalid")
    if set(candidate.diff) != {"patch", "sha256"}:
        raise ValueError("primary reviewer packet diff artifact is malformed")
    if set(candidate.tests) != {"receipt", "sha256"}:
        raise ValueError("primary reviewer packet test artifact is malformed")
    normalized_task = _primary_task_artifact(candidate.task)
    if normalized_task != dict(candidate.task):
        raise ValueError("primary reviewer packet task artifact is malformed")
    patch = candidate.diff.get("patch")
    patch_hash = candidate.diff.get("sha256")
    receipt = candidate.tests.get("receipt")
    receipt_hash = candidate.tests.get("sha256")
    if not isinstance(patch, str) or not isinstance(receipt, MappingABC):
        raise ValueError("primary reviewer packet raw artifacts are missing")
    normalized_receipt = _primary_test_artifact(receipt)
    if dict(receipt) != normalized_receipt:
        raise ValueError("primary reviewer packet test artifact is malformed")
    if hashlib.sha256(patch.encode("utf-8")).hexdigest() != patch_hash:
        raise ValueError("primary reviewer packet diff hash is invalid")
    if _sha256_json(normalized_receipt) != receipt_hash:
        raise ValueError("primary reviewer packet test hash is invalid")
    _reject_lead_outcome_fields(candidate.task, path="task")
    _reject_lead_outcome_fields(candidate.diff, path="diff")
    _reject_lead_outcome_fields(candidate.tests, path="tests")
    for artifact_name, artifact in (
        ("task", candidate.task),
        ("diff", candidate.diff),
        ("tests", candidate.tests),
    ):
        removed_paths: list[str] = []
        blinded = _scrub_arm_identity(
            artifact,
            path=artifact_name,
            removed_paths=removed_paths,
        )
        if removed_paths or blinded != dict(artifact):
            raise ValueError(
                "primary reviewer packet is not blinded at "
                + ", ".join(removed_paths or (artifact_name,))
            )
    body = {
        "schema_version": candidate.schema_version,
        "task": dict(candidate.task),
        "diff": dict(candidate.diff),
        "tests": dict(candidate.tests),
    }
    if candidate.packet_hash != _sha256_json(body):
        raise ValueError("primary reviewer packet hash is invalid")
    return candidate


def build_adjudicator_packet(
    *,
    experiment_id: str,
    task_id: str,
    task: TaskSpec | Mapping[str, Any],
    diff: str | None = None,
    tests: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    review_store: SqliteExperimentStore,
    adjudication_started_at_ms: int,
    lead_outcome: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        isinstance(adjudication_started_at_ms, bool)
        or not isinstance(adjudication_started_at_ms, int)
        or adjudication_started_at_ms <= 0
    ):
        raise ValueError(
            "adjudication_started_at_ms must be a positive integer"
        )
    primary_packet = build_primary_reviewer_packet(
        task=task,
        diff=diff,
        tests=tests,
        evidence=evidence,
    )
    primary_packet = validate_primary_reviewer_packet(primary_packet)
    receipts = review_store.get_primary_review_receipts(
        experiment_id,
        task_id,
    )
    if len(receipts) < 2:
        raise ValueError(
            "adjudication requires at least two persisted primary reviews"
        )
    for receipt in receipts:
        if receipt.primary_packet_hash != primary_packet["packet_hash"]:
            raise ValueError(
                "primary review receipt does not bind the reviewer packet"
            )
        if (
            receipt.completed_at_ms > receipt.persisted_at_ms
            or receipt.persisted_at_ms >= adjudication_started_at_ms
        ):
            raise ValueError(
                "primary reviews must complete and persist before adjudication"
            )
    return {
        "schema_version": "supervisor-outcome-aware-adjudication/v1",
        "experiment_id": experiment_id,
        "task_id": task_id,
        "primary_packet_hash": primary_packet["packet_hash"],
        "adjudication_started_at_ms": adjudication_started_at_ms,
        "task": dict(primary_packet.task),
        "diff": dict(primary_packet.diff),
        "tests": dict(primary_packet.tests),
        "primary_reviews": [
            receipt.to_dict() for receipt in receipts
        ],
        "lead_outcome": dict(lead_outcome),
    }


def _is_common_pre_treatment_failure(exc: Exception) -> bool:
    if not isinstance(exc, ArmExecutionError):
        return False
    if (
        exc.failure_classification
        != COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE
    ):
        return False
    return (
        exc.attempts == 0
        and exc.cost_usd == 0.0
        and not exc.token_usage
        and not exc.attempt_records
        and exc.metadata.get("pre_treatment") is True
        and exc.metadata.get("common_across_arms") is True
    )


def _common_pre_treatment_block_failure(
    *,
    arm: Arm,
    task: TaskSpec,
    verifier_version: str,
    assignment: Assignment,
    exc: Exception,
) -> ArmOutcome:
    failure_seed = "||".join(
        (
            assignment.assignment_id,
            "common-pre-treatment-infrastructure",
            arm.value,
            type(exc).__name__,
        )
    )
    original = FrozenTaskResult.create(
        task_id=task.task_id,
        task_family=task.task_family,
        task_spec_hash=task.spec_hash,
        run_result_hash=hashlib.sha256(
            failure_seed.encode("utf-8")
        ).hexdigest(),
        patch="",
        output="common pre-treatment infrastructure failure",
        metadata={
            "failure_classification": (
                COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE
            ),
            "exception_type": type(exc).__name__,
        },
    )
    blinded, removed_paths = _blind_frozen_result_with_audit(original)
    grade = Grade(
        verifier_id=task.verifier_id,
        verifier_version=verifier_version,
        verifier_hash=task.verifier_hash,
        frozen_result_hash=blinded.result_hash,
        passed=False,
        score=0.0,
        evidence={
            "intention_to_treat": False,
            "common_pre_treatment_infrastructure_failure": True,
            "exception_type": type(exc).__name__,
        },
        failure_classification=(
            COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE
        ),
    )
    return ArmOutcome(
        arm=arm,
        status="failed",
        grade=grade,
        attempts=0,
        cost_usd=0.0,
        latency_ms=0,
        frozen_result_hash=blinded.result_hash,
        original_frozen_result_hash=original.result_hash,
        blinding_removed_paths=removed_paths,
        failure_classification=(
            COMMON_PRE_TREATMENT_INFRASTRUCTURE_FAILURE
        ),
        error=f"{type(exc).__name__}: {exc}",
    )


def _intention_to_treat_failure(
    *,
    arm: Arm,
    task: TaskSpec,
    verifier_version: str,
    assignment: Assignment,
    exc: Exception,
) -> ArmOutcome:
    attempts = max(1, int(getattr(exc, "attempts", 1)))
    cost_usd = _non_negative_finite(
        getattr(exc, "cost_usd", 0.0),
        default=0.0,
    )
    latency_ms = max(0, int(getattr(exc, "latency_ms", 0)))
    token_usage = dict(getattr(exc, "token_usage", {}) or {})
    attempt_records = tuple(
        dict(record)
        for record in (getattr(exc, "attempt_records", ()) or ())
    )
    failure_classification = str(
        getattr(exc, "failure_classification", "")
        or "treatment_execution_failure"
    )
    failure_seed = "||".join(
        (
            assignment.assignment_id,
            arm.value,
            type(exc).__name__,
        )
    )
    original = FrozenTaskResult.create(
        task_id=task.task_id,
        task_family=task.task_family,
        task_spec_hash=task.spec_hash,
        run_result_hash=hashlib.sha256(failure_seed.encode("utf-8")).hexdigest(),
        patch="",
        output="execution failed before a candidate result was available",
        metadata={
            "failure_classification": "treatment_execution_failure",
            "exception_type": type(exc).__name__,
        },
    )
    blinded, removed_paths = _blind_frozen_result_with_audit(original)
    grade = Grade(
        verifier_id=task.verifier_id,
        verifier_version=verifier_version,
        verifier_hash=task.verifier_hash,
        frozen_result_hash=blinded.result_hash,
        passed=False,
        score=0.0,
        evidence={
            "intention_to_treat": True,
            "exception_type": type(exc).__name__,
            "attempts": attempts,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "token_usage": token_usage,
            "attempt_records": list(attempt_records),
        },
        failure_classification=failure_classification,
    )
    return ArmOutcome(
        arm=arm,
        status="failed",
        grade=grade,
        attempts=attempts,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        frozen_result_hash=blinded.result_hash,
        original_frozen_result_hash=original.result_hash,
        blinding_removed_paths=removed_paths,
        token_usage=token_usage,
        attempt_records=attempt_records,
        failure_classification=failure_classification,
        error=f"{type(exc).__name__}: {exc}",
    )


def _verification_failure(
    *,
    arm: Arm,
    task: TaskSpec,
    verifier_version: str,
    execution: ArmExecution,
    receipt: ArmExecutionReceipt,
    blinded_result: FrozenTaskResult,
    removed_paths: tuple[str, ...],
    exc: Exception,
) -> ArmOutcome:
    token_usage = dict(receipt.token_usage)
    attempt_records = tuple(dict(record) for record in receipt.attempt_records)
    grade = Grade(
        verifier_id=task.verifier_id,
        verifier_version=verifier_version,
        verifier_hash=task.verifier_hash,
        frozen_result_hash=blinded_result.result_hash,
        passed=False,
        score=0.0,
        evidence={
            "intention_to_treat": True,
            "exception_type": type(exc).__name__,
            "error": str(exc),
        },
        failure_classification="verifier_failure",
    )
    return ArmOutcome(
        arm=arm,
        status="failed",
        grade=grade,
        attempts=receipt.attempts,
        cost_usd=receipt.cost_usd,
        latency_ms=receipt.latency_ms,
        frozen_result_hash=blinded_result.result_hash,
        original_frozen_result_hash=execution.frozen_result.result_hash,
        blinding_removed_paths=removed_paths,
        execution_receipt=receipt,
        token_usage=token_usage,
        attempt_records=attempt_records,
        failure_classification="verifier_failure",
        error=f"{type(exc).__name__}: {exc}",
    )


def _assignment_stratum(
    experiment: ExperimentSpec,
    task: TaskSpec,
) -> dict[str, str]:
    stratum = {
        "canonical_repo_id": task.canonical_repo_id,
        "task_family": task.task_family,
        "task_class": task.task_class,
        "model": str(experiment.metadata.get("model") or ""),
    }
    roster = experiment.metadata.get("assignment_roster")
    if roster is not None:
        stratum["assignment_roster_hash"] = _sha256_json({
            "assignment_roster": list(roster),
        })
    return {
        **stratum,
        "stratum_id": _sha256_json(stratum),
    }


def _deterministic_stratum_position(
    experiment: ExperimentSpec,
    task: TaskSpec,
    *,
    stratum: Mapping[str, str],
) -> int:
    roster = experiment.metadata.get("assignment_roster")
    if roster is not None:
        if not isinstance(roster, Sequence) or isinstance(
            roster,
            (str, bytes),
        ):
            raise ValueError("assignment_roster must be a frozen sequence")
        entries = tuple(str(value).strip() for value in roster)
        if (
            not entries
            or any(not value for value in entries)
            or len(entries) != len(set(entries))
        ):
            raise ValueError(
                "assignment_roster must contain unique non-empty identities"
            )
        candidates = {
            canonical_task_identity(task),
            task.canonical_task_key,
            task.task_id,
        }
        positions = [
            index
            for index, value in enumerate(entries)
            if value in candidates
        ]
        if len(positions) != 1:
            raise ValueError(
                "TaskSpec must appear exactly once in assignment_roster"
            )
        return positions[0]
    rank_seed = "||".join(
        (
            experiment.experiment_id,
            experiment.assignment_version,
            stratum["stratum_id"],
            canonical_task_identity(task),
        )
    ).encode("utf-8")
    rank = hmac.new(
        experiment.hmac_key,
        rank_seed,
        hashlib.sha256,
    ).digest()
    return int.from_bytes(rank[:16], "big")


def _derive_assignment(
    experiment: ExperimentSpec,
    task: TaskSpec,
    *,
    stratum: Mapping[str, str],
    stratum_position: int,
) -> tuple[dict[str, str], str, tuple[Arm, Arm, Arm]]:
    if stratum_position < 0:
        raise ValueError("assignment stratum position must be non-negative")
    permuted_block_index, permuted_block_position = divmod(
        stratum_position,
        len(ARM_ORDERS),
    )
    cycle_seed = "||".join(
        (
            experiment.experiment_id,
            experiment.assignment_version,
            stratum["stratum_id"],
            str(permuted_block_index),
        )
    ).encode("utf-8")
    cycle = tuple(sorted(
        ARM_ORDERS,
        key=lambda candidate: hmac.new(
            experiment.hmac_key,
            cycle_seed
            + b"||"
            + ",".join(arm.value for arm in candidate).encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    ))
    order = cycle[permuted_block_position]
    assignment_method = (
        "frozen-roster-six-order/v1"
        if experiment.metadata.get("assignment_roster") is not None
        else "hmac-task-ranked-six-order/v2"
    )
    block = {
        **dict(stratum),
        "assignment_method": assignment_method,
        "stratum_position": str(stratum_position),
        "permuted_block_index": str(permuted_block_index),
        "permuted_block_position": str(permuted_block_position),
    }
    message = "||".join(
        (
            experiment.experiment_id,
            canonical_task_identity(task),
            experiment.assignment_version,
            json.dumps(block, sort_keys=True, separators=(",", ":")),
            ",".join(arm.value for arm in order),
        )
    ).encode("utf-8")
    digest = hmac.new(experiment.hmac_key, message, hashlib.sha256).hexdigest()
    return block, digest, order


def _validate_persisted_assignment(
    assignment: Assignment,
    *,
    experiment: ExperimentSpec,
    task: TaskSpec,
    expected_block: Mapping[str, str],
    expected_digest: str,
    expected_order: tuple[Arm, Arm, Arm],
) -> None:
    if assignment.experiment_id != experiment.experiment_id:
        raise ValueError("persisted assignment experiment_id does not match experiment")
    if assignment.task_id != task.task_id:
        raise ValueError("persisted assignment task_id does not match task")
    if assignment.canonical_task_id != canonical_task_identity(task):
        raise ValueError(
            "persisted assignment canonical task identity does not match task"
        )
    if assignment.assignment_version != experiment.assignment_version:
        raise ValueError("persisted assignment version does not match experiment")
    if not hmac.compare_digest(assignment.assignment_id, expected_digest):
        raise ValueError("persisted assignment id does not match deterministic HMAC")
    if not isinstance(assignment.block, MappingABC):
        raise ValueError("persisted assignment block is not a mapping")
    if dict(assignment.block) != dict(expected_block):
        raise ValueError(
            "persisted assignment block does not match deterministic HMAC input"
        )
    if assignment.order != expected_order:
        raise ValueError(
            "persisted assignment order does not match deterministic HMAC permutation"
        )
    if len(assignment.order) != len(Arm) or set(assignment.order) != set(Arm):
        raise ValueError("persisted assignment order is not an exact A/B/C permutation")


def _validate_task_verifier_pin(task: TaskSpec) -> None:
    if not isinstance(task.verifier_id, str) or not task.verifier_id.strip():
        raise ValueError("TaskSpec verifier_id must be a non-empty string")
    if (
        not isinstance(task.verifier_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", task.verifier_hash)
    ):
        raise ValueError(
            "TaskSpec verifier_hash must be a lowercase sha256 digest"
        )


def _validate_verifier_adapter(
    verifier: VerifierAdapter,
    *,
    task: TaskSpec,
) -> str:
    verifier_id = str(getattr(verifier, "verifier_id", "") or "").strip()
    verifier_version = str(
        getattr(verifier, "verifier_version", "") or ""
    ).strip()
    verifier_hash = str(
        getattr(verifier, "verifier_hash", "") or ""
    ).strip()
    if not verifier_id:
        raise ValueError("verifier adapter verifier_id must be non-empty")
    if verifier_id != task.verifier_id:
        raise ValueError("verifier adapter verifier_id does not match TaskSpec")
    if not verifier_version:
        raise ValueError("verifier adapter verifier_version must be non-empty")
    if not re.fullmatch(r"[0-9a-f]{64}", verifier_hash):
        raise ValueError(
            "verifier adapter verifier_hash must be a lowercase sha256 digest"
        )
    if verifier_hash != task.verifier_hash:
        raise ValueError("verifier adapter verifier_hash does not match TaskSpec")
    return verifier_version


def _extract_execution_receipt(
    execution: ArmExecution,
    *,
    allow_fixture_annotation_rebind: bool = False,
) -> ArmExecutionReceipt:
    if not isinstance(execution, ArmExecution):
        raise ValueError("executor must return an ArmExecution")
    if isinstance(execution.receipt, ArmExecutionReceipt):
        return execution.receipt
    raw = execution.metadata.get("execution_receipt")
    if not isinstance(raw, MappingABC):
        raise ValueError(
            "ArmExecution must carry a typed execution receipt"
        )
    receipt = ArmExecutionReceipt.from_mapping(raw)
    if (
        allow_fixture_annotation_rebind
        and receipt.frozen_result_hash
        != execution.frozen_result.result_hash
        and receipt.environment.mode == "hermetic"
        and receipt.environment.enforced is False
    ):
        receipt = replace(
            receipt,
            frozen_result_hash=execution.frozen_result.result_hash,
        )
    return receipt


def _validate_arm_execution_receipt(
    execution: ArmExecution,
    *,
    receipt: ArmExecutionReceipt,
    arm: Arm,
    task: TaskSpec,
    assignment: Assignment,
    budget: ArmBudget,
    expected_mode: str,
    observed_execution_ids: set[str],
    observed_result_ids: set[str],
    observed_isolation_ids: set[str],
    observed_workspace_ids: set[str],
    observed_session_ids: set[str],
    observed_cache_namespaces: set[str],
    observed_memory_namespaces: set[str],
    observed_lesson_namespaces: set[str],
    observed_environment_attestation_ids: set[str],
) -> None:
    if receipt.schema_version != ARM_EXECUTION_RECEIPT_SCHEMA_VERSION:
        raise ValueError("ArmExecution receipt schema is invalid")
    for field_name, value in (
        ("execution_id", receipt.execution_id),
        ("result_id", receipt.result_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"ArmExecution receipt {field_name} is required")
    if receipt.arm != arm:
        raise ValueError("ArmExecution receipt arm does not match assignment")
    if receipt.assignment_id != assignment.assignment_id:
        raise ValueError(
            "ArmExecution receipt assignment_id does not match assignment"
        )
    if receipt.task_id != task.task_id:
        raise ValueError("ArmExecution receipt task_id does not match TaskSpec")
    if receipt.canonical_task_id != canonical_task_identity(task):
        raise ValueError(
            "ArmExecution receipt canonical task identity does not match TaskSpec"
        )
    if receipt.task_spec_hash != task.spec_hash:
        raise ValueError(
            "ArmExecution receipt task_spec_hash does not match TaskSpec"
        )
    if receipt.frozen_result_hash != execution.frozen_result.result_hash:
        raise ValueError(
            "ArmExecution receipt frozen_result_hash does not match result"
        )
    attempts = _positive_integer(
        receipt.attempts,
        field="ArmExecution receipt attempts",
    )
    if attempts > budget.max_retries + 1:
        raise ValueError("ArmExecution receipt attempts exceed arm budget")
    cost_usd = _finite_non_negative_number(
        receipt.cost_usd,
        field="ArmExecution receipt cost_usd",
    )
    latency_ms = _non_negative_integer(
        receipt.latency_ms,
        field="ArmExecution receipt latency_ms",
    )
    if (
        execution.attempts != attempts
        or not math.isclose(
            float(execution.cost_usd),
            cost_usd,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        or execution.latency_ms != latency_ms
    ):
        raise ValueError(
            "ArmExecution accounting differs from its execution receipt"
        )
    aggregate_tokens = _validated_token_usage(
        receipt.token_usage,
        field="ArmExecution receipt token_usage",
    )
    if len(receipt.attempt_records) != attempts:
        raise ValueError(
            "ArmExecution receipt attempt count does not match attempt records"
        )
    attempt_cost = 0.0
    attempt_latency_ms = 0
    attempt_tokens = {"tokens_in": 0, "tokens_out": 0}
    attempt_run_ids: set[str] = set()
    attempt_session_ids: set[str] = set()
    for expected_index, record in enumerate(receipt.attempt_records):
        if not isinstance(record, MappingABC):
            raise ValueError("ArmExecution attempt record must be a mapping")
        if _non_negative_integer(
            record.get("attempt_index"),
            field="ArmExecution attempt_index",
        ) != expected_index:
            raise ValueError(
                "ArmExecution attempt indexes must be contiguous and ordered"
            )
        if str(record.get("execution_id") or "") != receipt.execution_id:
            raise ValueError(
                "ArmExecution attempt record execution_id is inconsistent"
            )
        record_cost = _finite_non_negative_number(
            record.get("cost_usd"),
            field="ArmExecution attempt cost_usd",
        )
        record_latency = _non_negative_integer(
            record.get("latency_ms"),
            field="ArmExecution attempt latency_ms",
        )
        record_tokens = _validated_token_usage(
            record.get("token_usage"),
            field="ArmExecution attempt token_usage",
        )
        status = str(record.get("status") or "").strip().casefold()
        if status not in {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
            "error",
        }:
            raise ValueError(
                "ArmExecution attempt status is invalid"
            )
        if (
            status == "completed"
            and expected_index != attempts - 1
        ):
            raise ValueError(
                "ArmExecution completed attempt must be final"
            )
        if (
            expected_index == attempts - 1
            and status != "completed"
        ):
            raise ValueError(
                "ArmExecution final attempt must be completed"
            )
        attempt_cost += record_cost
        attempt_latency_ms += record_latency
        for key in attempt_tokens:
            attempt_tokens[key] += record_tokens[key]
        run_id = str(record.get("run_id") or "").strip()
        session_id = str(record.get("session_id") or "").strip()
        if not run_id or not session_id:
            raise ValueError(
                "ArmExecution attempt record run/session identity is required"
            )
        if run_id in attempt_run_ids or session_id in attempt_session_ids:
            raise ValueError(
                "ArmExecution attempt run/session identity was reused"
            )
        attempt_run_ids.add(run_id)
        attempt_session_ids.add(session_id)
    if not math.isclose(
        attempt_cost,
        cost_usd,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("ArmExecution receipt has impossible cost accounting")
    if attempt_tokens != {
        "tokens_in": aggregate_tokens["tokens_in"],
        "tokens_out": aggregate_tokens["tokens_out"],
    }:
        raise ValueError("ArmExecution receipt has impossible token accounting")
    if attempt_latency_ms > latency_ms:
        raise ValueError("ArmExecution receipt has impossible latency accounting")
    total_tokens = (
        aggregate_tokens["tokens_in"] + aggregate_tokens["tokens_out"]
    )
    if total_tokens > budget.max_tokens:
        raise ValueError("ArmExecution receipt exceeds arm token budget")
    if cost_usd > budget.max_cost_usd:
        raise ValueError("ArmExecution receipt exceeds arm cost budget")
    if latency_ms > budget.timeout_s * 1000:
        raise ValueError("ArmExecution receipt exceeds arm time budget")

    isolation = receipt.isolation
    if isolation.schema_version != ISOLATION_ATTESTATION_SCHEMA_VERSION:
        raise ValueError("ArmExecution isolation attestation schema is invalid")
    if isolation.enforced is not True:
        raise ValueError("ArmExecution isolation must be backend-enforced")
    isolation_values = {
        "isolation_id": isolation.isolation_id,
        "workspace_id": isolation.workspace_id,
        "session_id": isolation.session_id,
        "cache_namespace": isolation.cache_namespace,
        "memory_namespace": isolation.memory_namespace,
        "lesson_namespace": isolation.lesson_namespace,
    }
    for field_name, value in isolation_values.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"ArmExecution isolation {field_name} is required"
            )
    if isolation.session_id not in attempt_session_ids:
        raise ValueError(
            "ArmExecution isolation session does not bind an attempt"
        )

    environment = receipt.environment
    _validate_execution_environment_attestation(
        environment,
        task=task,
        budget=budget,
        expected_mode=expected_mode,
    )
    _validate_runtime_manifest(
        receipt.runtime_manifest,
        operational=expected_mode == "operational",
    )

    unique_claims = (
        (receipt.execution_id, observed_execution_ids, "execution_id"),
        (receipt.result_id, observed_result_ids, "result_id"),
        (isolation.isolation_id, observed_isolation_ids, "isolation_id"),
        (isolation.workspace_id, observed_workspace_ids, "workspace_id"),
        (isolation.session_id, observed_session_ids, "session_id"),
        (
            isolation.cache_namespace,
            observed_cache_namespaces,
            "cache_namespace",
        ),
        (
            isolation.memory_namespace,
            observed_memory_namespaces,
            "memory_namespace",
        ),
        (
            isolation.lesson_namespace,
            observed_lesson_namespaces,
            "lesson_namespace",
        ),
        (
            environment.attestation_id,
            observed_environment_attestation_ids,
            "environment_attestation_id",
        ),
    )
    for value, observed, label in unique_claims:
        if value in observed:
            raise ValueError(
                f"ArmExecution shared {label} across isolated arms"
            )
    for value, observed, _label in unique_claims:
        observed.add(value)


def _validate_execution_environment_attestation(
    attestation: ExecutionEnvironmentAttestation,
    *,
    task: TaskSpec,
    budget: ArmBudget,
    expected_mode: str,
) -> None:
    if (
        attestation.schema_version
        != EXECUTION_ENVIRONMENT_ATTESTATION_SCHEMA_VERSION
    ):
        raise ValueError("execution environment attestation schema is invalid")
    for field_name, value in (
        ("attestation_id", attestation.attestation_id),
        ("backend", attestation.backend),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"execution environment attestation {field_name} is required"
            )
    if attestation.mode != expected_mode:
        raise ValueError(
            "execution environment attestation mode does not match experiment"
        )
    if expected_mode == "operational" and attestation.enforced is not True:
        raise ValueError(
            "operational execution requires backend-enforced environment pins"
        )
    if expected_mode != "operational" and attestation.enforced is not False:
        raise ValueError(
            "non-operational execution must not masquerade as operational"
        )
    expected = {
        "image_digest": task.image_digest,
        "architecture": task.architecture,
        "os_name": task.os_name,
        "network_policy": task.network_policy,
    }
    if expected_mode == "operational":
        if not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            task.image_digest.casefold(),
        ):
            raise ValueError(
                "operational TaskSpec image_digest must be sha256-pinned"
            )
        if not task.architecture.strip() or not task.os_name.strip():
            raise ValueError(
                "operational TaskSpec platform pins are incomplete"
            )
        if task.network_policy not in {
            "disabled",
            "restricted",
            "enabled",
        }:
            raise ValueError(
                "operational TaskSpec network policy is invalid"
            )
    observed = {
        "image_digest": attestation.image_digest,
        "architecture": attestation.architecture,
        "os_name": attestation.os_name,
        "network_policy": attestation.network_policy,
    }
    if observed != expected:
        raise ValueError(
            "execution environment attestation does not match TaskSpec pins"
        )
    required_limits = {
        **dict(task.resource_limits),
        "max_tokens": budget.max_tokens,
        "max_cost_usd": budget.max_cost_usd,
        "timeout_s": budget.timeout_s,
        "max_retries": budget.max_retries,
    }
    for key, value in required_limits.items():
        if (
            key not in attestation.resource_limits
            or attestation.resource_limits[key] != value
        ):
            raise ValueError(
                "execution environment attestation is missing declared "
                f"resource limit {key}"
            )


def _validate_runtime_manifest(
    manifest: Mapping[str, Any],
    *,
    operational: bool,
) -> None:
    if not isinstance(manifest, MappingABC):
        raise ValueError("runtime manifest must be a mapping")
    manifest_hash = str(manifest.get("manifest_sha256") or "")
    body = {
        str(key): value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    if (
        not re.fullmatch(r"[0-9a-f]{64}", manifest_hash)
        or _sha256_json(body) != manifest_hash
    ):
        raise ValueError("runtime manifest hash is invalid")
    if not operational:
        return
    if manifest.get("schema_version") != (
        "supervisor-agent-runtime-manifest/v1"
    ):
        raise ValueError("operational runtime manifest schema is invalid")
    if manifest.get("complete") is not True:
        raise ValueError("operational runtime manifest must be complete")
    route = manifest.get("provider_route")
    binary = manifest.get("binary")
    transport = manifest.get("transport")
    tools = manifest.get("tools")
    if not all(
        isinstance(value, MappingABC)
        for value in (route, binary, transport)
    ):
        raise ValueError("operational runtime manifest sections are missing")
    for field_name in ("provider", "route_kind", "endpoint", "model_request"):
        if not str(route.get(field_name) or "").strip():
            raise ValueError(
                f"operational runtime provider route lacks {field_name}"
            )
    if route.get("complete") is not True:
        raise ValueError("operational runtime provider route is incomplete")
    if (
        binary.get("complete") is not True
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(binary.get("sha256") or ""),
        )
    ):
        raise ValueError("operational runtime binary digest is incomplete")
    configuration = transport.get("configuration")
    if (
        transport.get("complete") is not True
        or not str(transport.get("implementation") or "").strip()
        or not isinstance(configuration, MappingABC)
        or not configuration
        or _sha256_json(dict(configuration))
        != str(transport.get("configuration_sha256") or "")
    ):
        raise ValueError(
            "operational runtime transport configuration is incomplete"
        )
    if (
        not isinstance(tools, Sequence)
        or isinstance(tools, (str, bytes))
        or any(
            not isinstance(tool, str) or not tool.strip()
            for tool in tools
        )
    ):
        raise ValueError(
            "operational runtime tools must be an explicit string sequence"
        )


def _validated_token_usage(
    value: Any,
    *,
    field: str,
) -> dict[str, int]:
    if not isinstance(value, MappingABC):
        raise ValueError(f"{field} must be a mapping")
    result: dict[str, int] = {}
    for key in ("tokens_in", "tokens_out"):
        observed = value.get(key)
        if (
            isinstance(observed, bool)
            or not isinstance(observed, int)
            or observed < 0
        ):
            raise ValueError(
                f"{field} {key} must be a non-negative integer"
            )
        result[key] = observed
    numeric_values: dict[str, int] = dict(result)
    for raw_key, observed in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError(f"{field} keys must be non-empty strings")
        if raw_key in result:
            continue
        if isinstance(observed, bool):
            raise ValueError(f"{field} values must not be booleans")
        if not isinstance(observed, (int, float)):
            raise ValueError(
                f"{field} values must be numeric token counts"
            )
        numeric = float(observed)
        if (
            not math.isfinite(numeric)
            or numeric < 0
            or not numeric.is_integer()
        ):
            raise ValueError(
                f"{field} numeric values must be finite "
                "non-negative integers"
            )
        numeric_values[raw_key] = int(numeric)
    for alias, canonical in (
        ("input_tokens", "tokens_in"),
        ("output_tokens", "tokens_out"),
    ):
        if (
            alias in numeric_values
            and numeric_values[alias] != numeric_values[canonical]
        ):
            raise ValueError(
                f"{field} {alias} does not match {canonical}"
            )
    return result


def _positive_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _non_negative_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _finite_non_negative_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite and non-negative")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field} must be finite and non-negative"
        ) from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return parsed


def _experiment_execution_mode(experiment: ExperimentSpec) -> str:
    raw = str(
        experiment.metadata.get("execution_mode")
        or experiment.metadata.get("mode")
        or "operational"
    ).strip().casefold()
    if raw == "operational":
        return "operational"
    if raw in {
        "fixture",
        "hermetic",
        "non-operational",
        "non_operational",
        "test",
    }:
        return "hermetic"
    raise ValueError(f"unsupported experiment execution mode: {raw}")


def _verifier_config_hash(
    task: TaskSpec,
    *,
    verifier_version: str,
) -> str:
    return _sha256_json({
        "schema_version": "supervisor-verifier-config/v1",
        "task_spec_hash": task.spec_hash,
        "verifier_id": task.verifier_id,
        "verifier_hash": task.verifier_hash,
        "verifier_version": verifier_version,
    })


def _validate_frozen_result_task_binding(
    result: FrozenTaskResult,
    task: TaskSpec,
) -> None:
    if not isinstance(result, FrozenTaskResult):
        raise ValueError("executor must return a FrozenTaskResult")
    if result.task_id != task.task_id:
        raise ValueError("FrozenTaskResult task_id does not match TaskSpec")
    if result.task_family != task.task_family:
        raise ValueError("FrozenTaskResult task_family does not match TaskSpec")
    if result.task_spec_hash != task.spec_hash:
        raise ValueError("FrozenTaskResult task_spec_hash does not match TaskSpec")
    canonical = FrozenTaskResult.create(
        task_id=result.task_id,
        task_family=result.task_family,
        task_spec_hash=result.task_spec_hash,
        run_result_hash=result.run_result_hash,
        patch=result.patch,
        output=result.output,
        metadata=result.metadata,
        frozen_at_ms=result.frozen_at_ms,
    )
    if result.schema_version != canonical.schema_version:
        raise ValueError("FrozenTaskResult schema_version is invalid")
    if result.patch_hash != canonical.patch_hash:
        raise ValueError("FrozenTaskResult patch_hash does not match its patch")
    if result.result_hash != canonical.result_hash:
        raise ValueError("FrozenTaskResult result_hash does not match its contents")


def _validate_grade_binding(
    grade: Grade,
    *,
    blinded_result: FrozenTaskResult,
    task: TaskSpec,
) -> None:
    if not isinstance(grade, Grade):
        raise ValueError("verifier must return a Grade")
    if grade.frozen_result_hash != blinded_result.result_hash:
        raise ValueError(
            "Grade frozen_result_hash does not match blinded FrozenTaskResult"
        )
    if grade.verifier_id != task.verifier_id:
        raise ValueError("Grade verifier_id does not match TaskSpec verifier_id")
    if grade.verifier_hash != task.verifier_hash:
        raise ValueError("Grade verifier_hash does not match TaskSpec verifier_hash")
    if not grade.verifier_version.strip():
        raise ValueError("Grade verifier_version must be non-empty")
    if not isinstance(grade.passed, bool):
        raise ValueError("Grade passed outcome must be a bool")
    if not math.isfinite(float(grade.score)) or not 0.0 <= grade.score <= 1.0:
        raise ValueError("Grade score must be finite and between 0 and 1")


def _frozen_result_from_dict(
    payload: Mapping[str, Any],
) -> FrozenTaskResult:
    frozen = FrozenTaskResult(
        task_id=str(payload["task_id"]),
        task_family=str(payload["task_family"]),
        task_spec_hash=str(payload["task_spec_hash"]),
        run_result_hash=str(payload["run_result_hash"]),
        patch=str(payload["patch"]),
        patch_hash=str(payload["patch_hash"]),
        output=str(payload["output"]),
        frozen_at_ms=int(payload["frozen_at_ms"]),
        result_hash=str(payload["result_hash"]),
        metadata=dict(payload.get("metadata") or {}),
        schema_version=str(
            payload.get("schema_version")
            or "supervisor-frozen-task-result/v1"
        ),
    )
    canonical = FrozenTaskResult.create(
        task_id=frozen.task_id,
        task_family=frozen.task_family,
        task_spec_hash=frozen.task_spec_hash,
        run_result_hash=frozen.run_result_hash,
        patch=frozen.patch,
        output=frozen.output,
        metadata=frozen.metadata,
        frozen_at_ms=frozen.frozen_at_ms,
    )
    if (
        frozen.schema_version != canonical.schema_version
        or frozen.patch_hash != canonical.patch_hash
        or frozen.result_hash != canonical.result_hash
    ):
        raise ValueError("persisted FrozenTaskResult integrity is invalid")
    return frozen


def _task_experiment_result_from_dict(
    payload: Mapping[str, Any],
) -> TaskExperimentResult:
    assignment_payload = payload["assignment"]
    if not isinstance(assignment_payload, MappingABC):
        raise TypeError("assignment must be a mapping")
    assignment = Assignment(
        experiment_id=str(assignment_payload["experiment_id"]),
        task_id=str(assignment_payload["task_id"]),
        canonical_task_id=str(assignment_payload["canonical_task_id"]),
        assignment_version=str(assignment_payload["assignment_version"]),
        assignment_id=str(assignment_payload["assignment_id"]),
        order=tuple(Arm(value) for value in assignment_payload["order"]),
        block=dict(assignment_payload["block"]),
        assigned_at_ms=int(assignment_payload["assigned_at_ms"]),
    )
    raw_outcomes = payload["outcomes"]
    if not isinstance(raw_outcomes, list):
        raise TypeError("outcomes must be a list")
    outcomes: list[ArmOutcome] = []
    for raw_outcome in raw_outcomes:
        if not isinstance(raw_outcome, MappingABC):
            raise TypeError("outcome must be a mapping")
        raw_grade = raw_outcome["grade"]
        if not isinstance(raw_grade, MappingABC):
            raise TypeError("grade must be a mapping")
        grade = Grade(
            verifier_id=str(raw_grade["verifier_id"]),
            verifier_version=str(raw_grade["verifier_version"]),
            verifier_hash=str(raw_grade["verifier_hash"]),
            frozen_result_hash=str(raw_grade["frozen_result_hash"]),
            passed=raw_grade["passed"],
            score=float(raw_grade["score"]),
            evidence=dict(raw_grade["evidence"]),
            failure_classification=str(
                raw_grade.get("failure_classification") or ""
            ),
            flake_classification=str(
                raw_grade.get("flake_classification") or ""
            ),
            schema_version=str(
                raw_grade.get("schema_version") or GRADE_SCHEMA_VERSION
            ),
        )
        blinding = raw_outcome.get("blinding")
        if not isinstance(blinding, MappingABC):
            blinding = {}
        raw_grade_revision = raw_outcome.get("grade_revision")
        if not isinstance(raw_grade_revision, MappingABC):
            raise TypeError(
                "persisted outcome must carry immutable grade lineage"
            )
        grade_revision = GradeRevisionRef.from_mapping(
            raw_grade_revision
        )
        raw_execution_receipt = raw_outcome.get("execution_receipt")
        execution_receipt = (
            ArmExecutionReceipt.from_mapping(raw_execution_receipt)
            if isinstance(raw_execution_receipt, MappingABC)
            else None
        )
        if (
            str(raw_outcome["status"]) == "completed"
            and execution_receipt is None
        ):
            raise TypeError(
                "completed persisted outcome lacks execution receipt"
            )
        outcomes.append(
            ArmOutcome(
                arm=Arm(raw_outcome["arm"]),
                status=str(raw_outcome["status"]),
                grade=grade,
                attempts=int(raw_outcome["attempts"]),
                cost_usd=float(raw_outcome["cost_usd"]),
                latency_ms=int(raw_outcome["latency_ms"]),
                frozen_result_hash=str(raw_outcome["frozen_result_hash"]),
                original_frozen_result_hash=str(
                    raw_outcome["original_frozen_result_hash"]
                ),
                blinding_removed_paths=tuple(
                    str(value)
                    for value in (blinding.get("removed_paths") or ())
                ),
                grade_revision=grade_revision,
                execution_receipt=execution_receipt,
                token_usage=dict(raw_outcome.get("token_usage") or {}),
                attempt_records=tuple(
                    dict(record)
                    for record in (
                        raw_outcome.get("attempt_records") or ()
                    )
                ),
                failure_classification=str(
                    raw_outcome.get("failure_classification") or ""
                ),
                error=str(raw_outcome.get("error") or ""),
            )
        )
    return TaskExperimentResult(
        experiment_id=str(payload["experiment_id"]),
        task_id=str(payload["task_id"]),
        assignment=assignment,
        outcomes=tuple(outcomes),
    )


def _non_negative_finite(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed) or parsed < 0:
        return default
    return parsed


def _scrub_arm_identity(
    value: Any,
    *,
    path: str,
    removed_paths: list[str],
) -> Any:
    if isinstance(value, MappingABC):
        scrubbed: dict[Any, Any] = {}
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _is_arm_identity_key(key):
                removed_paths.append(child_path)
                continue
            scrubbed[key] = _scrub_arm_identity(
                child,
                path=child_path,
                removed_paths=removed_paths,
            )
        return scrubbed
    if isinstance(value, list):
        return [
            _scrub_arm_identity(
                child,
                path=f"{path}[{index}]",
                removed_paths=removed_paths,
            )
            for index, child in enumerate(value)
        ]
    if isinstance(value, tuple):
        return tuple(
            _scrub_arm_identity(
                child,
                path=f"{path}[{index}]",
                removed_paths=removed_paths,
            )
            for index, child in enumerate(value)
        )
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError(f"unsupported verifier packet value at {path}")
    _reject_arm_identity_scalar(value, path=path)
    return value


def _is_arm_identity_key(key: Any) -> bool:
    normalized = _normalize_identity_text(str(key))
    tokens = set(normalized.split("_"))
    return bool(tokens & _ARM_IDENTITY_KEY_TOKENS) or normalized in {
        "harnessarm",
        "supervisor_enabled",
    }


def _reject_arm_identity_scalar(value: Any, *, path: str) -> None:
    if not isinstance(value, str):
        return
    normalized = _normalize_identity_text(value)
    if normalized in _ARM_IDENTITY_EXACT_VALUES:
        raise ValueError(f"arm identity leakage at {path}")
    if _UNAMBIGUOUS_ARM_IDENTITY_RE.search(value):
        raise ValueError(f"arm identity leakage at {path}")
    if _SUPERVISOR_CONTEXT_RE.search(value):
        raise ValueError(f"arm identity leakage at {path}")
    if _EXPLICIT_ARM_IDENTITY_RE.search(value):
        raise ValueError(f"arm identity leakage at {path}")
    if path == "run_result_hash":
        segments = set(normalized.split("_"))
        if "supervisor" in segments:
            raise ValueError(f"arm identity leakage at {path}")
        if "production_baseline" in normalized:
            raise ValueError(f"arm identity leakage at {path}")
        if "compute_matched_direct" in normalized:
            raise ValueError(f"arm identity leakage at {path}")


def _normalize_identity_text(value: str) -> str:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[^a-z0-9]+", "_", snake_case.casefold()).strip("_")


def _primary_task_artifact(
    task: TaskSpec | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(task, TaskSpec):
        return {
            "task_id": task.task_id,
            "task_family": task.task_family,
            "task_class": task.task_class,
            "task_spec_hash": task.spec_hash,
            "canonical_task_id": task.canonical_task_id,
            "canonical_repo_id": task.canonical_repo_id,
            "problem_statement": task.problem_statement,
        }
    if not isinstance(task, MappingABC):
        raise ValueError("primary review task artifact must be structured")
    allowed = {
        "task_id",
        "task_family",
        "task_class",
        "task_spec_hash",
        "canonical_task_id",
        "canonical_repo_id",
        "problem",
        "problem_statement",
    }
    unknown = set(task) - allowed
    if unknown:
        raise ValueError(
            "primary review task artifact contains non-raw fields: "
            + ", ".join(sorted(str(key) for key in unknown))
        )
    task_id = str(task.get("task_id") or "").strip()
    problem = str(
        task.get("problem_statement") or task.get("problem") or ""
    ).strip()
    if not task_id or not problem:
        raise ValueError(
            "primary review task artifact requires task_id and problem statement"
        )
    artifact = {
        str(key): value
        for key, value in task.items()
        if key in allowed and key != "problem"
    }
    artifact["task_id"] = task_id
    artifact["problem_statement"] = problem
    return artifact


def _primary_test_artifact(tests: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "command",
        "exit_code",
        "stdout",
        "stderr",
        "duration_ms",
    }
    allowed = required | {"result_files"}
    unknown = set(tests) - allowed
    missing = required - set(tests)
    if missing or unknown:
        raise ValueError(
            "primary review requires a raw test execution artifact; "
            "free-text summaries are not blinded proof"
        )
    if tests.get("schema_version") != RAW_TEST_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("primary review test artifact schema is invalid")
    command = tests.get("command")
    if (
        not isinstance(command, Sequence)
        or isinstance(command, (str, bytes))
        or not command
        or any(
            not isinstance(part, str) or not part.strip()
            for part in command
        )
    ):
        raise ValueError(
            "primary review test artifact command must be a non-empty sequence"
        )
    exit_code = tests.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ValueError(
            "primary review test artifact exit_code must be an integer"
        )
    duration_ms = tests.get("duration_ms")
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or duration_ms < 0
    ):
        raise ValueError(
            "primary review test artifact duration_ms must be non-negative"
        )
    stdout = tests.get("stdout")
    stderr = tests.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ValueError(
            "primary review test artifact streams must be text"
        )
    raw_result_files = tests.get("result_files", {})
    if not isinstance(raw_result_files, MappingABC):
        raise ValueError(
            "primary review test artifact result_files must be a mapping"
        )
    result_files: dict[str, str] = {}
    for raw_path, raw_hash in raw_result_files.items():
        path = str(raw_path).strip()
        digest = str(raw_hash).strip().casefold()
        if not path or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(
                "primary review test result files must pin sha256 digests"
            )
        result_files[path] = digest
    return {
        "schema_version": RAW_TEST_ARTIFACT_SCHEMA_VERSION,
        "command": [str(part) for part in command],
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "result_files": result_files,
    }


def _reject_lead_outcome_fields(value: Any, *, path: str) -> None:
    if isinstance(value, MappingABC):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            path_tokens = set(_normalize_identity_text(child_path).split("_"))
            if "lead" in path_tokens and path_tokens & {
                "outcome",
                "decision",
                "verdict",
                "accepted",
                "winner",
                "result",
            }:
                raise ValueError(
                    f"lead outcome leakage in primary reviewer packet at "
                    f"{child_path}"
                )
            _reject_lead_outcome_fields(child, path=child_path)
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_lead_outcome_fields(
                child,
                path=f"{path}[{index}]",
            )
        return
    if isinstance(value, str) and _SEMANTIC_OUTCOME_LEAK_RE.search(value):
        raise ValueError(
            f"lead outcome leakage in primary reviewer packet at {path}"
        )


def _sha256_json(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
