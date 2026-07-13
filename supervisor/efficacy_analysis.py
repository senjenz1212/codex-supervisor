"""Paired efficacy, pilot sizing, and operating-ROI calculations."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from statistics import fmean
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from .task_environment import canonical_task_identity

MIN_CONFIRMATION_POWER = 0.90
MIN_PREREGISTERED_B_WIN_RATE = 0.55
MAX_PREREGISTERED_B_WIN_RATE = 0.75
MAX_CONFIRMATION_ALPHA = 0.05
FROZEN_ITT_SNAPSHOT_SCHEMA_VERSION = "supervisor-frozen-itt-snapshot/v1"
_ITT_ARMS = ("B", "C")
_TERMINAL_OUTCOME_STATUSES = frozenset({
    "completed",
    "failed",
    "cancelled",
    "timed_out",
})


@dataclass(frozen=True)
class FrozenITTAssignment:
    task_id: str
    canonical_task_id: str
    assignment_id: str

    def __post_init__(self) -> None:
        task_id = str(self.task_id).strip()
        canonical_task_id = str(self.canonical_task_id).strip().lower()
        assignment_id = str(self.assignment_id).strip().lower()
        if not task_id:
            raise ValueError("authoritative assignment task_id is required")
        if not re.fullmatch(r"[0-9a-f]{64}", canonical_task_id):
            raise ValueError(
                "authoritative assignment canonical_task_id must be sha256"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", assignment_id):
            raise ValueError(
                "authoritative assignment assignment_id must be sha256"
            )
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "canonical_task_id", canonical_task_id)
        object.__setattr__(self, "assignment_id", assignment_id)

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "canonical_task_id": self.canonical_task_id,
            "assignment_id": self.assignment_id,
        }


@dataclass(frozen=True)
class FrozenTerminalOutcome:
    task_id: str
    canonical_task_id: str
    assignment_id: str
    arm: str
    status: str
    passed: bool

    def __post_init__(self) -> None:
        task_id = str(self.task_id).strip()
        canonical_task_id = str(self.canonical_task_id).strip().lower()
        assignment_id = str(self.assignment_id).strip().lower()
        arm = str(self.arm).strip().upper()
        status = str(self.status).strip().casefold()
        if not task_id:
            raise ValueError("authoritative terminal outcome task_id is required")
        if not re.fullmatch(r"[0-9a-f]{64}", canonical_task_id):
            raise ValueError(
                "authoritative outcome canonical_task_id must be sha256"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", assignment_id):
            raise ValueError(
                "authoritative outcome assignment_id must be sha256"
            )
        if arm not in _ITT_ARMS:
            raise ValueError("authoritative outcome arm must be B or C")
        if not status:
            raise ValueError("authoritative outcome status is required")
        if not isinstance(self.passed, bool):
            raise ValueError("authoritative outcome passed must be a bool")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "canonical_task_id", canonical_task_id)
        object.__setattr__(self, "assignment_id", assignment_id)
        object.__setattr__(self, "arm", arm)
        object.__setattr__(self, "status", status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "canonical_task_id": self.canonical_task_id,
            "assignment_id": self.assignment_id,
            "arm": self.arm,
            "status": self.status,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class FrozenITTSnapshot:
    experiment_id: str
    assignments: tuple[FrozenITTAssignment, ...]
    terminal_outcomes: tuple[FrozenTerminalOutcome, ...]
    frozen_at_ms: int
    snapshot_hash: str
    schema_version: str = FROZEN_ITT_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        experiment_id = str(self.experiment_id).strip()
        if not experiment_id:
            raise ValueError("authoritative ITT experiment_id is required")
        if (
            isinstance(self.frozen_at_ms, bool)
            or not isinstance(self.frozen_at_ms, int)
            or self.frozen_at_ms <= 0
        ):
            raise ValueError("authoritative ITT frozen_at_ms must be positive")
        if self.schema_version != FROZEN_ITT_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported authoritative ITT snapshot schema")
        digest = str(self.snapshot_hash).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("authoritative ITT snapshot_hash must be sha256")
        object.__setattr__(self, "experiment_id", experiment_id)
        object.__setattr__(self, "assignments", tuple(self.assignments))
        object.__setattr__(
            self,
            "terminal_outcomes",
            tuple(self.terminal_outcomes),
        )
        object.__setattr__(self, "snapshot_hash", digest)

    @classmethod
    def create(
        cls,
        *,
        experiment_id: str,
        assignments: Iterable[FrozenITTAssignment],
        terminal_outcomes: Iterable[FrozenTerminalOutcome],
        frozen_at_ms: int,
    ) -> "FrozenITTSnapshot":
        exact_assignments = tuple(assignments)
        exact_outcomes = tuple(terminal_outcomes)
        payload = _frozen_itt_snapshot_payload(
            experiment_id=str(experiment_id).strip(),
            assignments=exact_assignments,
            terminal_outcomes=exact_outcomes,
            frozen_at_ms=frozen_at_ms,
        )
        return cls(
            experiment_id=str(experiment_id).strip(),
            assignments=exact_assignments,
            terminal_outcomes=exact_outcomes,
            frozen_at_ms=frozen_at_ms,
            snapshot_hash=_sha256_json(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **_frozen_itt_snapshot_payload(
                experiment_id=self.experiment_id,
                assignments=self.assignments,
                terminal_outcomes=self.terminal_outcomes,
                frozen_at_ms=self.frozen_at_ms,
            ),
            "snapshot_hash": self.snapshot_hash,
        }


AuthoritativeITTSnapshotResolver = Callable[
    [str],
    FrozenITTSnapshot | None,
]


@dataclass(frozen=True)
class PairedAnalysis:
    n11: int
    n10: int
    n01: int
    n00: int
    discordant_pairs: int
    risk_difference: float
    newcombe_ci95: tuple[float, float]
    mcnemar_exact_p_value: float

    @property
    def task_count(self) -> int:
        return self.n11 + self.n10 + self.n01 + self.n00

    def to_dict(self) -> dict[str, Any]:
        return {
            "n11": self.n11,
            "n10": self.n10,
            "n01": self.n01,
            "n00": self.n00,
            "task_count": self.task_count,
            "discordant_pairs": self.discordant_pairs,
            "risk_difference": self.risk_difference,
            "newcombe_paired_risk_difference_ci95": list(self.newcombe_ci95),
            "mcnemar_exact_p_value": self.mcnemar_exact_p_value,
        }


@dataclass(frozen=True)
class PilotEstimate:
    task_count: int
    discordant_task_count: int
    verifier_flake_count: int
    infrastructure_failure_count: int
    mean_cost_by_arm: Mapping[str, float]
    mean_latency_ms_by_arm: Mapping[str, float]
    mean_risk_cost_by_arm: Mapping[str, float]
    task_ids: tuple[str, ...]
    canonical_task_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.task_count, bool)
            or self.task_count <= 0
            or not 0 <= self.discordant_task_count <= self.task_count
            or not 0 <= self.verifier_flake_count <= self.task_count
            or not 0 <= self.infrastructure_failure_count <= self.task_count
        ):
            raise ValueError("pilot aggregate counts are invalid")
        if (
            len(self.task_ids) != self.task_count
            or len(set(self.task_ids)) != self.task_count
        ):
            raise ValueError("pilot task_ids must be unique and complete")
        if (
            len(self.canonical_task_ids) != self.task_count
            or len(set(self.canonical_task_ids)) != self.task_count
            or any(
                not re.fullmatch(r"[0-9a-f]{64}", task_id)
                for task_id in self.canonical_task_ids
            )
        ):
            raise ValueError(
                "pilot canonical_task_ids must be unique canonical digests"
            )
        for field_name in (
            "mean_cost_by_arm",
            "mean_latency_ms_by_arm",
            "mean_risk_cost_by_arm",
        ):
            raw = getattr(self, field_name)
            if set(raw) != {"A", "B", "C"}:
                raise ValueError(
                    f"{field_name} must define exactly A, B, and C"
                )
            normalized = {
                arm: _nonnegative_number(
                    value,
                    field_name=f"{field_name}.{arm}",
                )
                for arm, value in raw.items()
            }
            object.__setattr__(
                self,
                field_name,
                MappingProxyType(normalized),
            )

    @property
    def discordance_rate(self) -> float:
        return self.discordant_task_count / max(1, self.task_count)

    @property
    def verifier_flake_rate(self) -> float:
        return self.verifier_flake_count / max(1, self.task_count)

    @property
    def infrastructure_failure_rate(self) -> float:
        return self.infrastructure_failure_count / max(1, self.task_count)


@dataclass(frozen=True)
class ConfirmationPlan:
    pilot_task_count: int
    pilot_task_set_hash: str
    pilot_discordance_rate: float
    conservative_discordance_rate: float
    discordance_bound_method: str
    alternative_b_win_rate: float
    alpha: float
    power: float
    required_discordant_pairs: int
    total_unique_tasks: int
    total_arm_runs: int
    plan_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pilot_task_count": self.pilot_task_count,
            "pilot_task_set_hash": self.pilot_task_set_hash,
            "pilot_discordance_rate": self.pilot_discordance_rate,
            "conservative_discordance_rate": (
                self.conservative_discordance_rate
            ),
            "discordance_bound_method": self.discordance_bound_method,
            "alternative_b_win_rate": self.alternative_b_win_rate,
            "alpha": self.alpha,
            "power": self.power,
            "required_discordant_pairs": self.required_discordant_pairs,
            "total_unique_tasks": self.total_unique_tasks,
            "total_arm_runs": self.total_arm_runs,
            "plan_hash": self.plan_hash,
        }


@dataclass(frozen=True)
class HarnessROI:
    task_count: int
    success_rate_delta: float
    incremental_successes: float
    incremental_operating_cost: float
    cost_per_incremental_success: float | None
    break_even_success_rate_delta: float
    classification: str


def analyze_paired_outcomes(
    rows: Iterable[Mapping[str, Any]],
    *,
    experiment_id: str,
    authoritative_snapshot_resolver: AuthoritativeITTSnapshotResolver,
    confidence: float = 0.95,
) -> PairedAnalysis:
    materialized, _, _ = _validate_paired_rows(
        rows,
        empty_error="paired analysis requires at least one unique task",
        experiment_id=experiment_id,
        authoritative_snapshot_resolver=authoritative_snapshot_resolver,
    )
    return _analyze_validated_paired_outcomes(
        materialized,
        confidence=confidence,
    )


def _analyze_validated_paired_outcomes(
    rows: Iterable[Mapping[str, Any]],
    *,
    confidence: float,
) -> PairedAnalysis:
    n11 = n10 = n01 = n00 = 0
    for row in rows:
        b_pass = row["b_pass"]
        c_pass = row["c_pass"]
        if b_pass and c_pass:
            n11 += 1
        elif b_pass:
            n10 += 1
        elif c_pass:
            n01 += 1
        else:
            n00 += 1
    total = n11 + n10 + n01 + n00
    if total <= 0:
        raise ValueError("paired analysis requires at least one unique task")
    difference = (n10 - n01) / total
    return PairedAnalysis(
        n11=n11,
        n10=n10,
        n01=n01,
        n00=n00,
        discordant_pairs=n10 + n01,
        risk_difference=difference,
        newcombe_ci95=newcombe_paired_risk_difference_ci(
            n11=n11,
            n10=n10,
            n01=n01,
            n00=n00,
            confidence=confidence,
        ),
        mcnemar_exact_p_value=exact_mcnemar_p_value(n10=n10, n01=n01),
    )


def exact_mcnemar_p_value(*, n10: int, n01: int) -> float:
    discordant = n10 + n01
    if discordant == 0:
        return 1.0
    smaller = min(n10, n01)
    lower_tail = sum(
        math.comb(discordant, successes)
        for successes in range(smaller + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * lower_tail)


def newcombe_paired_risk_difference_ci(
    *,
    n11: int,
    n10: int,
    n01: int,
    n00: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Newcombe's score interval for a difference of paired proportions.

    This is Newcombe (1998), method 10.  It combines marginal Wilson intervals
    with the phi correlation estimated from the paired 2x2 table.
    """
    n = n11 + n10 + n01 + n00
    if n <= 0:
        raise ValueError("paired interval requires at least one task")
    z = _normal_quantile(0.5 + confidence / 2.0)
    p1 = (n11 + n10) / n
    p2 = (n11 + n01) / n
    difference = p1 - p2
    l1, u1 = _wilson_interval(n11 + n10, n, z)
    l2, u2 = _wilson_interval(n11 + n01, n, z)

    phi = 0.0
    margins = (n11 + n10, n01 + n00, n11 + n01, n10 + n00)
    if max(margins) > 0:
        a = math.prod(margins)
        if a > 0:
            b = n11 * n00 - n10 * n01
            correction = b - n / 2 if b > n / 2 else (b if b < 0 else 0.0)
            phi = correction / math.sqrt(a)

    lower = difference - math.sqrt(
        max(
            0.0,
            (p1 - l1) ** 2
            - 2 * phi * (p1 - l1) * (u2 - p2)
            + (u2 - p2) ** 2,
        )
    )
    upper = difference + math.sqrt(
        max(
            0.0,
            (p2 - l2) ** 2
            - 2 * phi * (p2 - l2) * (u1 - p1)
            + (u1 - p1) ** 2,
        )
    )
    return max(-1.0, lower), min(1.0, upper)


def exact_discordant_pairs_required(
    *,
    win_rate: float,
    alpha: float = 0.05,
    power: float = 0.90,
    max_pairs: int = 100_000,
) -> int:
    if not 0.5 < win_rate < 1.0:
        raise ValueError("win_rate must be strictly between 0.5 and 1")
    if not 0.0 < alpha < 1.0 or not 0.0 < power < 1.0:
        raise ValueError("alpha and power must be probabilities")
    for discordant in range(1, max_pairs + 1):
        achieved = sum(
            _binomial_probability(discordant, wins, win_rate)
            for wins in range(discordant + 1)
            if exact_mcnemar_p_value(
                n10=wins,
                n01=discordant - wins,
            )
            <= alpha
        )
        if achieved >= power:
            return discordant
    raise ValueError("required discordant-pair count exceeds max_pairs")


def derive_confirmation_plan(
    pilot: PilotEstimate,
    *,
    alternative_b_win_rate: float,
    alpha: float = 0.05,
    power: float = 0.90,
) -> ConfirmationPlan:
    if pilot.task_count <= 0 or pilot.discordant_task_count <= 0:
        raise ValueError(
            "pilot must observe at least one discordant unique task before sizing"
        )
    if (
        len(pilot.task_ids) != pilot.task_count
        or len(pilot.canonical_task_ids) != pilot.task_count
        or len(set(pilot.canonical_task_ids)) != pilot.task_count
    ):
        raise ValueError(
            "pilot task roster must contain one unique canonical identity "
            "per task"
        )
    if (
        not MIN_PREREGISTERED_B_WIN_RATE
        <= alternative_b_win_rate
        <= MAX_PREREGISTERED_B_WIN_RATE
    ):
        raise ValueError(
            "alternative_b_win_rate must stay within the preregistered "
            f"[{MIN_PREREGISTERED_B_WIN_RATE:.2f}, "
            f"{MAX_PREREGISTERED_B_WIN_RATE:.2f}] range"
        )
    if not 0.0 < alpha <= MAX_CONFIRMATION_ALPHA:
        raise ValueError(
            f"confirmation alpha must be in (0, {MAX_CONFIRMATION_ALPHA:.2f}]"
        )
    if not MIN_CONFIRMATION_POWER <= power < 1.0:
        raise ValueError(
            f"confirmation power must be at least {MIN_CONFIRMATION_POWER:.2f}"
        )
    required = exact_discordant_pairs_required(
        win_rate=alternative_b_win_rate,
        alpha=alpha,
        power=power,
    )
    conservative_discordance_rate = _wilson_interval(
        pilot.discordant_task_count,
        pilot.task_count,
        _normal_quantile(0.975),
    )[0]
    if conservative_discordance_rate <= 0.0:
        raise ValueError(
            "pilot discordance lower confidence bound is zero; "
            "confirmation size is not identifiable"
        )
    total_tasks = math.ceil(required / conservative_discordance_rate)
    payload = {
        "pilot_task_count": pilot.task_count,
        "pilot_task_set_hash": _sha256_json(
            sorted(pilot.canonical_task_ids)
        ),
        "pilot_discordance_rate": pilot.discordance_rate,
        "conservative_discordance_rate": conservative_discordance_rate,
        "discordance_bound_method": "wilson-lower-95",
        "alternative_b_win_rate": alternative_b_win_rate,
        "alpha": alpha,
        "power": power,
        "required_discordant_pairs": required,
        "total_unique_tasks": total_tasks,
        "total_arm_runs": total_tasks * 3,
    }
    return ConfirmationPlan(
        **{
            key: payload[key]
            for key in (
                "pilot_task_count",
                "pilot_task_set_hash",
                "pilot_discordance_rate",
                "conservative_discordance_rate",
                "discordance_bound_method",
                "alternative_b_win_rate",
                "alpha",
                "power",
                "required_discordant_pairs",
                "total_unique_tasks",
                "total_arm_runs",
            )
        },
        plan_hash=_sha256_json(payload),
    )


def require_disjoint_task_sets(
    pilot_tasks: Iterable[Any],
    confirmation_tasks: Iterable[Any],
) -> None:
    pilot_task_id_list = [
        canonical_task_identity(task) for task in pilot_tasks
    ]
    confirmation_task_id_list = [
        canonical_task_identity(task) for task in confirmation_tasks
    ]
    if len(pilot_task_id_list) != len(set(pilot_task_id_list)):
        raise ValueError(
            "pilot task set contains canonical task aliases"
        )
    if len(confirmation_task_id_list) != len(
        set(confirmation_task_id_list)
    ):
        raise ValueError(
            "confirmation task set contains canonical task aliases"
        )
    pilot_task_ids = set(pilot_task_id_list)
    confirmation_task_ids = set(confirmation_task_id_list)
    overlap = sorted(pilot_task_ids & confirmation_task_ids)
    if overlap:
        raise ValueError(
            "pilot/confirmation canonical task overlap: "
            + ", ".join(overlap[:10])
        )


def compute_harness_roi(
    *,
    task_count: int,
    success_b: float,
    success_c: float,
    cost_b: float,
    cost_c: float,
    latency_cost_per_task: float,
    expected_risk_cost_per_task: float,
    value_per_verified_success: float,
) -> HarnessROI:
    if (
        isinstance(task_count, bool)
        or not isinstance(task_count, int)
        or task_count <= 0
    ):
        raise ValueError("task_count must be positive")
    success_b = _bounded_rate(success_b, field_name="success_b")
    success_c = _bounded_rate(success_c, field_name="success_c")
    cost_b = _nonnegative_number(cost_b, field_name="cost_b")
    cost_c = _nonnegative_number(cost_c, field_name="cost_c")
    latency_cost_per_task = _nonnegative_number(
        latency_cost_per_task,
        field_name="latency_cost_per_task",
    )
    expected_risk_cost_per_task = _nonnegative_number(
        expected_risk_cost_per_task,
        field_name="expected_risk_cost_per_task",
    )
    value_per_verified_success = _finite_number(
        value_per_verified_success,
        field_name="value_per_verified_success",
    )
    if value_per_verified_success <= 0.0:
        raise ValueError("value_per_verified_success must be positive")
    delta = success_b - success_c
    successes = task_count * delta
    incremental_cost = task_count * (cost_b - cost_c)
    cost_per_success = (
        incremental_cost / successes
        if successes > 0
        else None
    )
    total_burden_per_task = (
        (cost_b - cost_c)
        + latency_cost_per_task
        + expected_risk_cost_per_task
    )
    break_even = total_burden_per_task / value_per_verified_success
    if delta <= 0:
        classification = "no_positive_harness_effect"
    elif delta >= break_even:
        classification = "harness_effect_positive_roi_positive"
    else:
        classification = "harness_effect_positive_roi_negative"
    return HarnessROI(
        task_count=task_count,
        success_rate_delta=delta,
        incremental_successes=successes,
        incremental_operating_cost=incremental_cost,
        cost_per_incremental_success=cost_per_success,
        break_even_success_rate_delta=break_even,
        classification=classification,
    )


def estimate_pilot(
    rows: Iterable[Mapping[str, Any]],
    *,
    experiment_id: str,
    authoritative_snapshot_resolver: AuthoritativeITTSnapshotResolver,
) -> PilotEstimate:
    materialized, task_ids, canonical_task_ids = _validate_paired_rows(
        rows,
        empty_error="pilot requires unique task rows",
        experiment_id=experiment_id,
        authoritative_snapshot_resolver=authoritative_snapshot_resolver,
    )
    paired = _analyze_validated_paired_outcomes(
        materialized,
        confidence=0.95,
    )
    operating_values: dict[str, dict[str, list[float]]] = {
        metric: {"A": [], "B": [], "C": []}
        for metric in ("cost", "latency_ms", "risk_cost")
    }
    for row in materialized:
        for metric, values_by_arm in operating_values.items():
            for arm, values in values_by_arm.items():
                field_name = f"{metric}_{arm.lower()}"
                if field_name not in row:
                    raise ValueError(
                        f"pilot row {row['task_id']} is missing {field_name}"
                    )
                raw_value = row[field_name]
                if isinstance(raw_value, bool):
                    raise ValueError(
                        f"{field_name} must be finite and non-negative"
                    )
                try:
                    value = float(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{field_name} must be finite and non-negative"
                    ) from exc
                if not math.isfinite(value) or value < 0:
                    raise ValueError(
                        f"{field_name} must be finite and non-negative"
                    )
                values.append(value)
    return PilotEstimate(
        task_count=len(materialized),
        discordant_task_count=paired.discordant_pairs,
        verifier_flake_count=sum(
            1 for row in materialized if bool(row.get("verifier_flake"))
        ),
        infrastructure_failure_count=sum(
            1 for row in materialized if bool(row.get("infrastructure_failure"))
        ),
        mean_cost_by_arm={
            arm: fmean(values)
            for arm, values in operating_values["cost"].items()
        },
        mean_latency_ms_by_arm={
            arm: fmean(values)
            for arm, values in operating_values["latency_ms"].items()
        },
        mean_risk_cost_by_arm={
            arm: fmean(values)
            for arm, values in operating_values["risk_cost"].items()
        },
        task_ids=task_ids,
        canonical_task_ids=canonical_task_ids,
    )


def _validate_paired_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    empty_error: str,
    experiment_id: str,
    authoritative_snapshot_resolver: AuthoritativeITTSnapshotResolver,
) -> tuple[list[Mapping[str, Any]], tuple[str, ...], tuple[str, ...]]:
    materialized = list(rows)
    if not materialized:
        raise ValueError(empty_error)
    assignments, outcomes = _resolve_authoritative_itt(
        experiment_id=experiment_id,
        authoritative_snapshot_resolver=authoritative_snapshot_resolver,
    )

    task_ids: list[str] = []
    canonical_task_ids: list[str] = []
    seen_task_ids: set[str] = set()
    seen_canonical_task_ids: set[str] = set()
    for index, row in enumerate(materialized):
        if not isinstance(row, Mapping):
            raise ValueError(f"paired row {index} must be a mapping")
        attempt_fields = [
            str(key)
            for key in row
            if _is_attempt_field(str(key))
        ]
        if attempt_fields:
            raise ValueError(
                "paired analysis rejects attempt-level fields: "
                + ", ".join(sorted(attempt_fields))
            )
        if "task_id" not in row:
            raise ValueError(f"paired row {index} is missing task_id")
        task_id = row["task_id"]
        if (
            not isinstance(task_id, str)
            or not task_id
            or task_id != task_id.strip()
        ):
            raise ValueError("task_id must be a stable non-empty string")
        if task_id in seen_task_ids:
            raise ValueError(
                f"paired rows require one unique task_id per row: {task_id}"
            )
        seen_task_ids.add(task_id)
        task_ids.append(task_id)
        assignment = assignments.get(task_id)
        if assignment is None:
            raise ValueError(
                f"paired row is not in authoritative assignments: {task_id}"
            )

        for outcome_field in ("b_pass", "c_pass"):
            if outcome_field not in row:
                raise ValueError(
                    f"paired row {task_id} is missing {outcome_field}"
                )
            if not isinstance(row[outcome_field], bool):
                raise ValueError(f"{outcome_field} must be a bool")
        task_identity = row.get("task_identity")
        if not isinstance(task_identity, Mapping):
            raise ValueError(
                "paired rows require a task_identity mapping"
            )
        canonical_task_id = canonical_task_identity(task_identity)
        if canonical_task_id != assignment.canonical_task_id:
            raise ValueError(
                f"paired row canonical task identity mismatch for {task_id}"
            )
        assignment_id = str(row.get("assignment_id") or "").strip().lower()
        if assignment_id != assignment.assignment_id:
            raise ValueError(
                f"paired row assignment_id mismatch for {task_id}"
            )
        if canonical_task_id in seen_canonical_task_ids:
            raise ValueError(
                "paired rows require one canonical task identity per row"
            )
        seen_canonical_task_ids.add(canonical_task_id)
        canonical_task_ids.append(canonical_task_id)
        for classification_field in (
            "verifier_flake",
            "infrastructure_failure",
        ):
            if (
                classification_field in row
                and not isinstance(row[classification_field], bool)
            ):
                raise ValueError(f"{classification_field} must be a bool")
        for prefix, arm in (("b", "B"), ("c", "C")):
            outcome = outcomes[(task_id, arm)]
            if row[f"{prefix}_pass"] is not outcome.passed:
                raise ValueError(
                    f"paired row {prefix}_pass disagrees with authoritative "
                    f"terminal outcome for {task_id}"
                )

    observed_task_ids = set(task_ids)
    expected_task_ids = set(assignments)
    omitted = sorted(expected_task_ids - observed_task_ids)
    extra = sorted(observed_task_ids - expected_task_ids)
    if omitted or extra:
        details = []
        if omitted:
            details.append("omitted=" + ",".join(omitted[:10]))
        if extra:
            details.append("extra=" + ",".join(extra[:10]))
        raise ValueError(
            "paired rows must exactly reconcile the authoritative ITT roster: "
            + "; ".join(details)
        )

    return materialized, tuple(task_ids), tuple(canonical_task_ids)


def _resolve_authoritative_itt(
    *,
    experiment_id: str,
    authoritative_snapshot_resolver: AuthoritativeITTSnapshotResolver,
) -> tuple[
    dict[str, FrozenITTAssignment],
    dict[tuple[str, str], FrozenTerminalOutcome],
]:
    normalized_experiment_id = str(experiment_id).strip()
    if not normalized_experiment_id:
        raise ValueError("experiment_id is required for authoritative ITT")
    if not callable(authoritative_snapshot_resolver):
        raise ValueError(
            "an authoritative ITT snapshot resolver is required"
        )
    try:
        snapshot = authoritative_snapshot_resolver(
            normalized_experiment_id
        )
    except Exception as exc:
        raise ValueError(
            "authoritative ITT snapshot resolution failed"
        ) from exc
    if not isinstance(snapshot, FrozenITTSnapshot):
        raise ValueError("authoritative ITT snapshot is unavailable")
    if snapshot.experiment_id != normalized_experiment_id:
        raise ValueError("authoritative ITT experiment_id mismatch")
    expected_hash = _sha256_json(
        _frozen_itt_snapshot_payload(
            experiment_id=snapshot.experiment_id,
            assignments=snapshot.assignments,
            terminal_outcomes=snapshot.terminal_outcomes,
            frozen_at_ms=snapshot.frozen_at_ms,
        )
    )
    if snapshot.snapshot_hash != expected_hash:
        raise ValueError("authoritative ITT snapshot hash mismatch")

    assignments: dict[str, FrozenITTAssignment] = {}
    canonical_task_ids: set[str] = set()
    assignment_ids: set[str] = set()
    for assignment in snapshot.assignments:
        if not isinstance(assignment, FrozenITTAssignment):
            raise ValueError(
                "authoritative ITT assignments must be frozen records"
            )
        if assignment.task_id in assignments:
            raise ValueError("duplicate authoritative assignment task")
        if assignment.canonical_task_id in canonical_task_ids:
            raise ValueError(
                "authoritative assignments contain aliased canonical tasks"
            )
        if assignment.assignment_id in assignment_ids:
            raise ValueError("duplicate authoritative assignment_id")
        assignments[assignment.task_id] = assignment
        canonical_task_ids.add(assignment.canonical_task_id)
        assignment_ids.add(assignment.assignment_id)
    if not assignments:
        raise ValueError("authoritative ITT assignments are empty")

    outcomes: dict[tuple[str, str], FrozenTerminalOutcome] = {}
    for outcome in snapshot.terminal_outcomes:
        if not isinstance(outcome, FrozenTerminalOutcome):
            raise ValueError(
                "authoritative ITT outcomes must be frozen records"
            )
        key = (outcome.task_id, outcome.arm)
        if key in outcomes:
            raise ValueError("duplicate authoritative terminal outcome")
        assignment = assignments.get(outcome.task_id)
        if assignment is None:
            raise ValueError(
                "authoritative outcome references an extra task"
            )
        if (
            outcome.canonical_task_id
            != assignment.canonical_task_id
            or outcome.assignment_id != assignment.assignment_id
        ):
            raise ValueError(
                "authoritative outcome assignment identity mismatch"
            )
        if outcome.status not in _TERMINAL_OUTCOME_STATUSES:
            raise ValueError(
                "authoritative ITT contains an unterminated task outcome"
            )
        if outcome.status != "completed" and outcome.passed:
            raise ValueError(
                "non-completed authoritative outcome cannot pass"
            )
        outcomes[key] = outcome

    expected_outcome_keys = {
        (task_id, arm)
        for task_id in assignments
        for arm in _ITT_ARMS
    }
    missing_outcomes = sorted(expected_outcome_keys - set(outcomes))
    extra_outcomes = sorted(set(outcomes) - expected_outcome_keys)
    if missing_outcomes or extra_outcomes:
        raise ValueError(
            "authoritative ITT requires exactly one terminal B and C "
            "outcome for every assignment"
        )
    return assignments, outcomes


def _frozen_itt_snapshot_payload(
    *,
    experiment_id: str,
    assignments: Iterable[FrozenITTAssignment],
    terminal_outcomes: Iterable[FrozenTerminalOutcome],
    frozen_at_ms: int,
) -> dict[str, Any]:
    return {
        "schema_version": FROZEN_ITT_SNAPSHOT_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "assignments": [
            assignment.to_dict()
            for assignment in assignments
        ],
        "terminal_outcomes": [
            outcome.to_dict()
            for outcome in terminal_outcomes
        ],
        "frozen_at_ms": frozen_at_ms,
    }


def _is_attempt_field(key: str) -> bool:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        snake_case.casefold(),
    ).strip("_")
    return bool({"attempt", "attempts"} & set(normalized.split("_")))


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite number")
    return numeric


def _bounded_rate(value: Any, *, field_name: str) -> float:
    numeric = _finite_number(value, field_name=field_name)
    if not 0.0 <= numeric <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return numeric


def _nonnegative_number(value: Any, *, field_name: str) -> float:
    numeric = _finite_number(value, field_name=field_name)
    if numeric < 0.0:
        raise ValueError(f"{field_name} must be non-negative")
    return numeric


def _wilson_interval(successes: int, total: int, z: float) -> tuple[float, float]:
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half_width = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - half_width), min(1.0, center + half_width)


def _normal_quantile(probability: float) -> float:
    # Peter John Acklam's inverse-normal approximation.
    if not 0.0 < probability < 1.0:
        raise ValueError("normal quantile probability must be in (0, 1)")
    a = (
        -39.69683028665376,
        220.9460984245205,
        -275.9285104469687,
        138.357751867269,
        -30.66479806614716,
        2.506628277459239,
    )
    b = (
        -54.47609879822406,
        161.5858368580409,
        -155.6989798598866,
        66.80131188771972,
        -13.28068155288572,
    )
    c = (
        -0.007784894002430293,
        -0.3223964580411365,
        -2.400758277161838,
        -2.549732539343734,
        4.374664141464968,
        2.938163982698783,
    )
    d = (
        0.007784695709041462,
        0.3224671290700398,
        2.445134137142996,
        3.754408661907416,
    )
    low = 0.02425
    high = 1 - low
    if probability < low:
        q = math.sqrt(-2 * math.log(probability))
        return (
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    if probability > high:
        q = math.sqrt(-2 * math.log(1 - probability))
        return -(
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    q = probability - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def _binomial_probability(n: int, k: int, p: float) -> float:
    return math.comb(n, k) * (p**k) * ((1 - p) ** (n - k))


def _sha256_json(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
