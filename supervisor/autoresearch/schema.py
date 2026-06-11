"""Schemas for ledger-backed, report-only supervisor AutoResearch attempts."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal


AUTORESEARCH_SCHEMA_VERSION = "supervisor-autoresearch/v1"
AUTORESEARCH_REPORT_SCHEMA_VERSION = "supervisor-autoresearch-report/v1"

ValidationStatus = Literal["pending", "accepted", "rejected", "blocked", "failed"]


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_json(value: Any) -> str:
    return sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AutoresearchExperiment:
    experiment_id: str
    task_id: str
    hypothesis: str
    baseline_ref: str
    mutable_paths: tuple[str, ...]
    immutable_paths: tuple[str, ...]
    evaluator_ref: str
    evaluator_hash: str
    metric_name: str
    max_attempts: int = 1
    k_trials: int = 1
    budget_usd: float = 0.0
    timeout_s: float = 60.0
    execution_mode: str = "fixture_replay"

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "AutoresearchExperiment":
        return cls(
            experiment_id=str(raw["experiment_id"]),
            task_id=str(raw["task_id"]),
            hypothesis=str(raw.get("hypothesis") or ""),
            baseline_ref=str(raw.get("baseline_ref") or ""),
            mutable_paths=tuple(str(path) for path in raw.get("mutable_paths", ())),
            immutable_paths=tuple(str(path) for path in raw.get("immutable_paths", ())),
            evaluator_ref=str(raw.get("evaluator_ref") or ""),
            evaluator_hash=str(raw.get("evaluator_hash") or ""),
            metric_name=str(raw.get("metric_name") or "score"),
            max_attempts=int(raw.get("max_attempts") or 1),
            k_trials=int(raw.get("k_trials") or 1),
            budget_usd=float(raw.get("budget_usd") or 0.0),
            timeout_s=float(raw.get("timeout_s") or 60.0),
            execution_mode=str(raw.get("execution_mode") or "fixture_replay"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AUTORESEARCH_SCHEMA_VERSION,
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "hypothesis": self.hypothesis,
            "baseline_ref": self.baseline_ref,
            "mutable_paths": list(self.mutable_paths),
            "immutable_paths": list(self.immutable_paths),
            "evaluator_ref": self.evaluator_ref,
            "evaluator_hash": self.evaluator_hash,
            "metric_name": self.metric_name,
            "max_attempts": self.max_attempts,
            "k_trials": self.k_trials,
            "budget_usd": self.budget_usd,
            "timeout_s": self.timeout_s,
            "execution_mode": self.execution_mode,
        }


@dataclass(frozen=True)
class AutoresearchAttempt:
    attempt_id: str
    experiment_id: str
    task_id: str
    worker_id: str
    hypothesis: str
    changed_files: tuple[str, ...]
    metric_trials: tuple[float, ...]
    parent_attempt_id: str | None = None
    metric_before: float | None = None
    metric_after: float | None = None
    metric_delta: float | None = None
    policy_overlay_candidate_ref: str = ""
    policy_candidate_changes: dict[str, str] = field(default_factory=dict)
    metric_source: str = "fixture"
    evaluator_run_ref: str = ""
    evaluator_run_hash: str = ""
    attempt_worktree_ref: str = ""
    patch_ref: str = ""
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    execution_errors: tuple[str, ...] = field(default_factory=tuple)
    cost_usd: float = 0.0
    wall_clock_s: float = 0.0
    status: ValidationStatus = "pending"

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, experiment: AutoresearchExperiment) -> "AutoresearchAttempt":
        return cls(
            attempt_id=str(raw["attempt_id"]),
            experiment_id=str(raw.get("experiment_id") or experiment.experiment_id),
            task_id=str(raw.get("task_id") or experiment.task_id),
            worker_id=str(raw.get("worker_id") or ""),
            hypothesis=str(raw.get("hypothesis") or ""),
            changed_files=tuple(str(path) for path in raw.get("changed_files", ())),
            metric_trials=tuple(float(value) for value in raw.get("metric_trials", ())),
            parent_attempt_id=(
                str(raw["parent_attempt_id"])
                if raw.get("parent_attempt_id") not in {None, ""}
                else None
            ),
            metric_before=_optional_float(raw.get("metric_before", raw.get("baseline_metric"))),
            metric_after=_optional_float(raw.get("metric_after", raw.get("candidate_metric"))),
            metric_delta=_optional_float(raw.get("metric_delta")),
            policy_overlay_candidate_ref=str(raw.get("policy_overlay_candidate_ref") or raw.get("candidate_overlay_ref") or ""),
            policy_candidate_changes={
                str(key): str(value)
                for key, value in dict(
                    raw.get("policy_candidate_changes") or raw.get("candidate_changes") or {}
                ).items()
            },
            metric_source=str(raw.get("metric_source") or "fixture"),
            evaluator_run_ref=str(raw.get("evaluator_run_ref") or ""),
            evaluator_run_hash=str(raw.get("evaluator_run_hash") or ""),
            attempt_worktree_ref=str(raw.get("attempt_worktree_ref") or ""),
            patch_ref=str(raw.get("patch_ref") or ""),
            artifact_hashes={str(key): str(value) for key, value in dict(raw.get("artifact_hashes") or {}).items()},
            evidence_refs=tuple(str(ref) for ref in raw.get("evidence_refs", ())),
            execution_errors=tuple(str(error) for error in raw.get("execution_errors", ())),
            cost_usd=float(raw.get("cost_usd") or 0.0),
            wall_clock_s=float(raw.get("wall_clock_s") or 0.0),
            status=str(raw.get("status") or "pending"),  # type: ignore[arg-type]
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AUTORESEARCH_SCHEMA_VERSION,
            "attempt_id": self.attempt_id,
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "hypothesis": self.hypothesis,
            "parent_attempt_id": self.parent_attempt_id,
            "metric_before": self.metric_before,
            "metric_after": self.metric_after,
            "metric_delta": self.metric_delta,
            "policy_overlay_candidate_ref": self.policy_overlay_candidate_ref,
            "policy_candidate_changes": dict(sorted(self.policy_candidate_changes.items())),
            "metric_source": self.metric_source,
            "evaluator_run_ref": self.evaluator_run_ref,
            "evaluator_run_hash": self.evaluator_run_hash,
            "attempt_worktree_ref": self.attempt_worktree_ref,
            "patch_ref": self.patch_ref,
            "changed_files": list(self.changed_files),
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
            "evidence_refs": list(self.evidence_refs),
            "execution_errors": list(self.execution_errors),
            "metric_trials": list(self.metric_trials),
            "cost_usd": self.cost_usd,
            "wall_clock_s": self.wall_clock_s,
            "status": self.status,
        }


@dataclass(frozen=True)
class AutoresearchValidationReport:
    experiment_id: str
    task_id: str
    attempt_id: str
    validation_status: ValidationStatus
    recommendation: str
    metric_name: str
    metric_trials: tuple[float, ...]
    metric_source: str
    evaluator_run_ref: str
    evaluator_run_hash: str
    metric_before: float | None
    metric_after: float | None
    metric_delta: float | None
    policy_overlay_candidate_ref: str
    policy_candidate_changes: dict[str, str]
    metric_median: float | None
    metric_iqr: float | None
    quality_unstable_across_trials: bool
    changed_files: tuple[str, ...]
    mutable_paths: tuple[str, ...]
    immutable_paths: tuple[str, ...]
    artifact_hashes: dict[str, str]
    gaming_flags: tuple[str, ...] = field(default_factory=tuple)
    validation_errors: tuple[str, ...] = field(default_factory=tuple)
    cost_usd: float = 0.0
    wall_clock_s: float = 0.0
    default_change_allowed: bool = False
    policy_mutated: bool = False
    gate_advanced: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AUTORESEARCH_REPORT_SCHEMA_VERSION,
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "validation_status": self.validation_status,
            "recommendation": self.recommendation,
            "metric_name": self.metric_name,
            "metric_trials": list(self.metric_trials),
            "metric_source": self.metric_source,
            "evaluator_run_ref": self.evaluator_run_ref,
            "evaluator_run_hash": self.evaluator_run_hash,
            "metric_before": self.metric_before,
            "metric_after": self.metric_after,
            "metric_delta": self.metric_delta,
            "policy_overlay_candidate_ref": self.policy_overlay_candidate_ref,
            "policy_candidate_changes": dict(sorted(self.policy_candidate_changes.items())),
            "metric_median": self.metric_median,
            "metric_iqr": self.metric_iqr,
            "quality_unstable_across_trials": self.quality_unstable_across_trials,
            "changed_files": list(self.changed_files),
            "mutable_paths": list(self.mutable_paths),
            "immutable_paths": list(self.immutable_paths),
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
            "gaming_flags": list(self.gaming_flags),
            "validation_errors": list(self.validation_errors),
            "cost_usd": self.cost_usd,
            "wall_clock_s": self.wall_clock_s,
            "default_change_allowed": False,
            "policy_mutated": bool(self.policy_mutated),
            "gate_advanced": bool(self.gate_advanced),
            "report_sha256": self.report_sha256(),
        }

    def report_sha256(self) -> str:
        payload = self.to_payload_without_hash()
        return sha256_json(payload)

    def to_payload_without_hash(self) -> dict[str, Any]:
        payload = {
            "schema_version": AUTORESEARCH_REPORT_SCHEMA_VERSION,
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "validation_status": self.validation_status,
            "recommendation": self.recommendation,
            "metric_name": self.metric_name,
            "metric_trials": list(self.metric_trials),
            "metric_source": self.metric_source,
            "evaluator_run_ref": self.evaluator_run_ref,
            "evaluator_run_hash": self.evaluator_run_hash,
            "metric_before": self.metric_before,
            "metric_after": self.metric_after,
            "metric_delta": self.metric_delta,
            "policy_overlay_candidate_ref": self.policy_overlay_candidate_ref,
            "policy_candidate_changes": dict(sorted(self.policy_candidate_changes.items())),
            "metric_median": self.metric_median,
            "metric_iqr": self.metric_iqr,
            "quality_unstable_across_trials": self.quality_unstable_across_trials,
            "changed_files": list(self.changed_files),
            "mutable_paths": list(self.mutable_paths),
            "immutable_paths": list(self.immutable_paths),
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
            "gaming_flags": list(self.gaming_flags),
            "validation_errors": list(self.validation_errors),
            "cost_usd": self.cost_usd,
            "wall_clock_s": self.wall_clock_s,
            "default_change_allowed": False,
            "policy_mutated": bool(self.policy_mutated),
            "gate_advanced": bool(self.gate_advanced),
        }
        return payload


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)
