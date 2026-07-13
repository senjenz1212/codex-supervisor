from __future__ import annotations

import math

import pytest

from supervisor.efficacy_analysis import (
    PilotEstimate,
    analyze_paired_outcomes,
    compute_harness_roi,
    derive_confirmation_plan,
    estimate_pilot,
    exact_discordant_pairs_required,
    require_disjoint_task_sets,
)


def _row(task_id: str, *, b_pass: bool, c_pass: bool) -> dict[str, object]:
    return {
        "task_id": task_id,
        "b_pass": b_pass,
        "c_pass": c_pass,
    }


def test_exact_two_sided_mcnemar_power_table_matches_the_preregistered_values() -> None:
    assert exact_discordant_pairs_required(win_rate=0.60) == 263
    assert exact_discordant_pairs_required(win_rate=0.65) == 114
    assert exact_discordant_pairs_required(win_rate=0.70) == 65
    assert exact_discordant_pairs_required(win_rate=0.75) == 42


def test_paired_analysis_reports_task_level_table_exact_test_and_newcombe_ci() -> None:
    rows = (
        *(
            _row(f"both-pass-{index}", b_pass=True, c_pass=True)
            for index in range(20)
        ),
        *(
            _row(f"b-only-{index}", b_pass=True, c_pass=False)
            for index in range(12)
        ),
        *(
            _row(f"c-only-{index}", b_pass=False, c_pass=True)
            for index in range(3)
        ),
        *(
            _row(f"both-fail-{index}", b_pass=False, c_pass=False)
            for index in range(15)
        ),
    )

    analysis = analyze_paired_outcomes(list(rows))

    assert analysis.n11 == 20
    assert analysis.n10 == 12
    assert analysis.n01 == 3
    assert analysis.n00 == 15
    assert analysis.discordant_pairs == 15
    assert analysis.risk_difference == pytest.approx((12 - 3) / 50)
    assert 0.0 <= analysis.mcnemar_exact_p_value <= 1.0
    assert analysis.newcombe_ci95[0] < analysis.risk_difference
    assert analysis.newcombe_ci95[1] > analysis.risk_difference


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        (
            [
                _row("duplicate", b_pass=True, c_pass=False),
                _row("duplicate", b_pass=False, c_pass=True),
            ],
            "unique task_id",
        ),
        ([{"b_pass": True, "c_pass": False}], "task_id"),
        ([_row("", b_pass=True, c_pass=False)], "stable non-empty string"),
        (
            [{"task_id": 7, "b_pass": True, "c_pass": False}],
            "stable non-empty string",
        ),
        (
            [{"task_id": "task-1", "b_pass": "false", "c_pass": False}],
            "b_pass must be a bool",
        ),
        (
            [{"task_id": "task-1", "b_pass": True, "c_pass": "true"}],
            "c_pass must be a bool",
        ),
        (
            [
                {
                    **_row("task-1", b_pass=True, c_pass=False),
                    "attempt": 1,
                }
            ],
            "attempt-level",
        ),
        (
            [
                {
                    **_row("task-1", b_pass=True, c_pass=False),
                    "b_attempts": 2,
                }
            ],
            "attempt-level",
        ),
        (
            [
                {
                    **_row("task-1", b_pass=True, c_pass=False),
                    "attemptCount": 2,
                }
            ],
            "attempt-level",
        ),
    ],
)
def test_paired_analysis_rejects_unstable_attempt_level_or_coerced_rows(
    rows: list[dict[str, object]],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        analyze_paired_outcomes(rows)


def test_pilot_rejects_string_boolean_classifications() -> None:
    with pytest.raises(ValueError, match="verifier_flake must be a bool"):
        estimate_pilot(
            [
                {
                    **_row("task-1", b_pass=True, c_pass=False),
                    "verifier_flake": "false",
                }
            ]
        )


def test_confirmation_size_is_frozen_from_pilot_discordance_not_attempt_count() -> None:
    pilot = PilotEstimate(
        task_count=80,
        discordant_task_count=16,
        verifier_flake_count=1,
        infrastructure_failure_count=2,
        mean_cost_by_arm={"B": 1.2, "C": 1.0},
        mean_latency_ms_by_arm={"B": 1200.0, "C": 1000.0},
        task_ids=tuple(f"pilot-{index}" for index in range(80)),
    )

    plan = derive_confirmation_plan(
        pilot,
        alternative_b_win_rate=0.65,
    )

    assert plan.required_discordant_pairs == 114
    assert plan.pilot_discordance_rate == pytest.approx(0.20)
    assert plan.total_unique_tasks == 570
    assert plan.total_arm_runs == 1710
    assert plan.plan_hash


def test_pilot_and_confirmation_task_sets_must_be_disjoint() -> None:
    require_disjoint_task_sets({"pilot-a"}, {"confirm-a"})
    with pytest.raises(ValueError, match="overlap"):
        require_disjoint_task_sets({"shared"}, {"shared"})


def test_roi_uses_b_minus_compute_matched_c_not_b_minus_baseline_a() -> None:
    roi = compute_harness_roi(
        task_count=1000,
        success_b=0.42,
        success_c=0.37,
        cost_b=1.30,
        cost_c=1.00,
        latency_cost_per_task=0.02,
        expected_risk_cost_per_task=0.01,
        value_per_verified_success=20.0,
    )

    assert roi.incremental_successes == pytest.approx(50.0)
    assert roi.incremental_operating_cost == pytest.approx(300.0)
    assert roi.cost_per_incremental_success == pytest.approx(6.0)
    assert roi.break_even_success_rate_delta == pytest.approx(0.0165)
    assert roi.classification == "harness_effect_positive_roi_positive"
