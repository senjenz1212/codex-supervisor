"""Preregistered A/B/C task-efficacy experiment kernel."""
from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import re
import sqlite3
import time
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .task_environment import FrozenTaskResult, Grade, TaskSpec, VerifierAdapter


class Arm(str, Enum):
    A = "production_baseline"
    B = "supervisor"
    C = "compute_matched_direct"


ARM_ORDERS: tuple[tuple[Arm, Arm, Arm], ...] = tuple(
    itertools.permutations((Arm.A, Arm.B, Arm.C))
)

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


@dataclass(frozen=True)
class ArmBudget:
    max_tokens: int
    max_cost_usd: float
    timeout_s: int
    max_retries: int

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
        missing = set(Arm) - set(self.arm_budgets)
        if missing:
            raise ValueError(
                "experiment missing arm budgets: "
                + ", ".join(sorted(arm.value for arm in missing))
            )
        if self.arm_budgets[Arm.B] != self.arm_budgets[Arm.C]:
            raise ValueError("arms B and C must have identical ex-ante resource ceilings")
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
    assignment_version: str
    assignment_id: str
    order: tuple[Arm, Arm, Arm]
    block: Mapping[str, str]
    assigned_at_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "assignment_version": self.assignment_version,
            "assignment_id": self.assignment_id,
            "order": [arm.value for arm in self.order],
            "block": dict(self.block),
            "assigned_at_ms": self.assigned_at_ms,
        }


@dataclass(frozen=True)
class ArmExecution:
    frozen_result: FrozenTaskResult
    attempts: int
    cost_usd: float
    latency_ms: int
    metadata: Mapping[str, Any] = field(default_factory=dict)


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
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiment_assignments (
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              assignment_version TEXT NOT NULL,
              assignment_id TEXT NOT NULL,
              order_json TEXT NOT NULL,
              block_json TEXT NOT NULL,
              assigned_at_ms INTEGER NOT NULL,
              PRIMARY KEY(experiment_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS experiment_task_results (
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              result_json TEXT NOT NULL,
              recorded_at_ms INTEGER NOT NULL,
              PRIMARY KEY(experiment_id, task_id)
            );
            """
        )
        self._conn.commit()

    def put_assignment(self, assignment: Assignment) -> Assignment:
        self._conn.execute(
            """INSERT OR IGNORE INTO experiment_assignments(
                 experiment_id, task_id, assignment_version, assignment_id,
                 order_json, block_json, assigned_at_ms
               ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (
                assignment.experiment_id,
                assignment.task_id,
                assignment.assignment_version,
                assignment.assignment_id,
                json.dumps([arm.value for arm in assignment.order]),
                json.dumps(dict(assignment.block), sort_keys=True),
                assignment.assigned_at_ms,
            ),
        )
        self._conn.commit()
        existing = self.get_assignment(assignment.experiment_id, assignment.task_id)
        if existing is None:
            raise RuntimeError("assignment persistence failed")
        if (
            existing.assignment_version != assignment.assignment_version
            or existing.assignment_id != assignment.assignment_id
        ):
            raise ValueError(
                "task already assigned under a different assignment version"
            )
        if existing.order != assignment.order:
            raise ValueError("persisted assignment order differs from insertion")
        if not isinstance(existing.block, MappingABC):
            raise ValueError("persisted assignment block is not a mapping")
        if dict(existing.block) != dict(assignment.block):
            raise ValueError("persisted assignment block differs from insertion")
        return existing

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
            assignment_version=row["assignment_version"],
            assignment_id=row["assignment_id"],
            order=order,
            block=block_payload,
            assigned_at_ms=int(row["assigned_at_ms"]),
        )

    def put_result(self, result: TaskExperimentResult) -> None:
        payload = json.dumps(
            result.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
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
        self._conn.commit()


class ExperimentKernel:
    def __init__(
        self,
        *,
        store: SqliteExperimentStore,
        executor: ArmExecutor,
    ) -> None:
        self.store = store
        self.executor = executor

    def assign(self, experiment: ExperimentSpec, task: TaskSpec) -> Assignment:
        block, digest, order = _derive_assignment(experiment, task)
        existing = self.store.get_assignment(experiment.experiment_id, task.task_id)
        if existing is not None:
            _validate_persisted_assignment(
                existing,
                experiment=experiment,
                task=task,
                expected_block=block,
                expected_digest=digest,
                expected_order=order,
            )
            return existing
        assignment = Assignment(
            experiment_id=experiment.experiment_id,
            task_id=task.task_id,
            assignment_version=experiment.assignment_version,
            assignment_id=digest,
            order=order,
            block=block,
            assigned_at_ms=int(time.time() * 1000),
        )
        persisted = self.store.put_assignment(assignment)
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
        _validate_task_verifier_pin(task)
        assignment = self.assign(experiment, task)
        outcomes: list[ArmOutcome] = []
        for arm in assignment.order:
            try:
                execution = await self.executor.execute(
                    arm=arm,
                    task=task,
                    budget=experiment.arm_budgets[arm],
                    assignment_id=assignment.assignment_id,
                )
            except Exception as exc:
                outcomes.append(
                    _intention_to_treat_failure(
                        arm=arm,
                        task=task,
                        verifier=verifier,
                        assignment=assignment,
                        exc=exc,
                    )
                )
                continue

            _validate_frozen_result_task_binding(execution.frozen_result, task)
            blinded, removed_paths = _blind_frozen_result_with_audit(
                execution.frozen_result
            )
            grade = await verifier.verify(blinded)
            _validate_grade_binding(
                grade,
                blinded_result=blinded,
                task=task,
            )
            outcomes.append(
                ArmOutcome(
                    arm=arm,
                    status="completed",
                    grade=grade,
                    attempts=max(1, int(execution.attempts)),
                    cost_usd=float(execution.cost_usd),
                    latency_ms=max(0, int(execution.latency_ms)),
                    frozen_result_hash=blinded.result_hash,
                    original_frozen_result_hash=execution.frozen_result.result_hash,
                    blinding_removed_paths=removed_paths,
                )
            )
        result = TaskExperimentResult(
            experiment_id=experiment.experiment_id,
            task_id=task.task_id,
            assignment=assignment,
            outcomes=tuple(outcomes),
        )
        self.store.put_result(result)
        return result


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
    task: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    blinded_task = _scrub_arm_identity(task, path="task", removed_paths=[])
    blinded_evidence = _scrub_arm_identity(
        evidence,
        path="evidence",
        removed_paths=[],
    )
    return {
        "schema_version": "supervisor-blinded-primary-review/v1",
        "task": dict(blinded_task),
        "evidence": dict(blinded_evidence),
    }


def build_adjudicator_packet(
    *,
    task: Mapping[str, Any],
    evidence: Mapping[str, Any],
    primary_reviews: list[Mapping[str, Any]],
    lead_outcome: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "supervisor-outcome-aware-adjudication/v1",
        "task": dict(task),
        "evidence": dict(evidence),
        "primary_reviews": [dict(review) for review in primary_reviews],
        "lead_outcome": dict(lead_outcome),
    }


def _intention_to_treat_failure(
    *,
    arm: Arm,
    task: TaskSpec,
    verifier: VerifierAdapter,
    assignment: Assignment,
    exc: Exception,
) -> ArmOutcome:
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
        verifier_version=str(getattr(verifier, "verifier_version", "unknown")),
        verifier_hash=task.verifier_hash,
        frozen_result_hash=blinded.result_hash,
        passed=False,
        score=0.0,
        evidence={
            "intention_to_treat": True,
            "exception_type": type(exc).__name__,
        },
        failure_classification="treatment_execution_failure",
    )
    return ArmOutcome(
        arm=arm,
        status="failed",
        grade=grade,
        attempts=1,
        cost_usd=0.0,
        latency_ms=0,
        frozen_result_hash=blinded.result_hash,
        original_frozen_result_hash=original.result_hash,
        blinding_removed_paths=removed_paths,
        failure_classification="treatment_execution_failure",
        error=f"{type(exc).__name__}: {exc}",
    )


def _derive_assignment(
    experiment: ExperimentSpec,
    task: TaskSpec,
) -> tuple[dict[str, str], str, tuple[Arm, Arm, Arm]]:
    block = {
        "repo": task.repo,
        "task_family": task.task_family,
        "model": str(experiment.metadata.get("model") or ""),
    }
    message = "||".join(
        (
            experiment.experiment_id,
            task.task_id,
            experiment.assignment_version,
            json.dumps(block, sort_keys=True, separators=(",", ":")),
        )
    ).encode("utf-8")
    digest = hmac.new(experiment.hmac_key, message, hashlib.sha256).hexdigest()
    order = ARM_ORDERS[int(digest[:16], 16) % len(ARM_ORDERS)]
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
    if not isinstance(task.verifier_hash, str) or not task.verifier_hash.strip():
        raise ValueError("TaskSpec verifier_hash must be a non-empty string")


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
    if not isinstance(grade.passed, bool):
        raise ValueError("Grade passed outcome must be a bool")


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
