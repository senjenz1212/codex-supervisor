"""Paired efficacy, pilot sizing, and operating-ROI calculations."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Iterable, Mapping


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
    task_ids: tuple[str, ...]

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
    pilot_discordance_rate: float
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
            "pilot_discordance_rate": self.pilot_discordance_rate,
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
    confidence: float = 0.95,
) -> PairedAnalysis:
    materialized, _ = _validate_paired_rows(
        rows,
        empty_error="paired analysis requires at least one unique task",
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
    required = exact_discordant_pairs_required(
        win_rate=alternative_b_win_rate,
        alpha=alpha,
        power=power,
    )
    total_tasks = math.ceil(required / pilot.discordance_rate)
    payload = {
        "pilot_task_count": pilot.task_count,
        "pilot_task_ids_hash": _sha256_json(sorted(pilot.task_ids)),
        "pilot_discordance_rate": pilot.discordance_rate,
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
                "pilot_discordance_rate",
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
    pilot_task_ids: Iterable[str],
    confirmation_task_ids: Iterable[str],
) -> None:
    overlap = sorted(set(pilot_task_ids) & set(confirmation_task_ids))
    if overlap:
        raise ValueError(
            "pilot/confirmation task overlap: " + ", ".join(overlap[:10])
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
    if task_count <= 0:
        raise ValueError("task_count must be positive")
    if value_per_verified_success <= 0:
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
) -> PilotEstimate:
    materialized, task_ids = _validate_paired_rows(
        rows,
        empty_error="pilot requires unique task rows",
    )
    paired = _analyze_validated_paired_outcomes(
        materialized,
        confidence=0.95,
    )
    costs: dict[str, list[float]] = {"A": [], "B": [], "C": []}
    latencies: dict[str, list[float]] = {"A": [], "B": [], "C": []}
    for row in materialized:
        for arm in costs:
            costs[arm].append(float(row.get(f"cost_{arm.lower()}") or 0.0))
            latencies[arm].append(
                float(row.get(f"latency_ms_{arm.lower()}") or 0.0)
            )
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
            arm: fmean(values) for arm, values in costs.items()
        },
        mean_latency_ms_by_arm={
            arm: fmean(values) for arm, values in latencies.items()
        },
        task_ids=task_ids,
    )


def _validate_paired_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    empty_error: str,
) -> tuple[list[Mapping[str, Any]], tuple[str, ...]]:
    materialized = list(rows)
    if not materialized:
        raise ValueError(empty_error)

    task_ids: list[str] = []
    seen_task_ids: set[str] = set()
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

        for outcome_field in ("b_pass", "c_pass"):
            if outcome_field not in row:
                raise ValueError(
                    f"paired row {task_id} is missing {outcome_field}"
                )
            if not isinstance(row[outcome_field], bool):
                raise ValueError(f"{outcome_field} must be a bool")
        for classification_field in (
            "verifier_flake",
            "infrastructure_failure",
        ):
            if (
                classification_field in row
                and not isinstance(row[classification_field], bool)
            ):
                raise ValueError(f"{classification_field} must be a bool")

    return materialized, tuple(task_ids)


def _is_attempt_field(key: str) -> bool:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        snake_case.casefold(),
    ).strip("_")
    return bool({"attempt", "attempts"} & set(normalized.split("_")))


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
