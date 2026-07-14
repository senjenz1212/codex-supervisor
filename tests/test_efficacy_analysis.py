from __future__ import annotations

from hashlib import sha256
import math

import pytest

from supervisor.efficacy_analysis import (
    FrozenITTAssignment,
    FrozenITTSnapshot,
    FrozenTerminalOutcome,
    PilotEstimate,
    analyze_paired_outcomes,
    compute_harness_roi,
    derive_confirmation_plan,
    estimate_pilot,
    exact_discordant_pairs_required,
    require_disjoint_task_sets,
)
from supervisor.task_environment import canonical_task_identity


_EXPERIMENT_ID = "efficacy-test-experiment"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _row(task_id: str, *, b_pass: bool, c_pass: bool) -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_identity": _task_identity(task_id or "empty-task-alias"),
        "assignment_id": _digest(f"assignment:{task_id}"),
        "b_pass": b_pass,
        "c_pass": c_pass,
    }


def _task_identity(canonical_task_key: str) -> dict[str, str]:
    return {
        "repo": "https://github.com/Unity-Technologies/example.git",
        "canonical_repo_id": (
            "https://github.com/Unity-Technologies/example.git"
        ),
        "revision": "1" * 40,
        "dataset_hash": "2" * 64,
        "split_hash": "3" * 64,
        "canonical_task_key": canonical_task_key,
    }


def _snapshot(
    rows: list[dict[str, object]],
    *,
    experiment_id: str = _EXPERIMENT_ID,
    status_by_task_arm: dict[tuple[str, str], str] | None = None,
) -> FrozenITTSnapshot:
    assignments: list[FrozenITTAssignment] = []
    outcomes: list[FrozenTerminalOutcome] = []
    for row in rows:
        task_id = str(row["task_id"])
        task_identity = row["task_identity"]
        assert isinstance(task_identity, dict)
        canonical_task_id = canonical_task_identity(task_identity)
        assignment_id = str(row["assignment_id"])
        assignments.append(
            FrozenITTAssignment(
                task_id=task_id,
                canonical_task_id=canonical_task_id,
                assignment_id=assignment_id,
            )
        )
        for arm, field in (("B", "b_pass"), ("C", "c_pass")):
            passed = row[field]
            assert isinstance(passed, bool)
            outcomes.append(
                FrozenTerminalOutcome(
                    task_id=task_id,
                    canonical_task_id=canonical_task_id,
                    assignment_id=assignment_id,
                    arm=arm,
                    status=(status_by_task_arm or {}).get(
                        (task_id, arm),
                        "completed",
                    ),
                    passed=passed,
                )
            )
    return FrozenITTSnapshot.create(
        experiment_id=experiment_id,
        assignments=assignments,
        terminal_outcomes=outcomes,
        frozen_at_ms=1_720_000_000_000,
    )


def _authority_kwargs(
    rows: list[dict[str, object]],
    *,
    snapshot: FrozenITTSnapshot | None = None,
    experiment_id: str = _EXPERIMENT_ID,
) -> dict[str, object]:
    authoritative = snapshot or _snapshot(
        rows,
        experiment_id=experiment_id,
    )
    return {
        "experiment_id": experiment_id,
        "authoritative_snapshot_resolver": (
            lambda requested: (
                authoritative
                if requested == experiment_id
                else None
            )
        ),
    }


def _analyze(
    rows: list[dict[str, object]],
    *,
    authority_rows: list[dict[str, object]] | None = None,
    snapshot: FrozenITTSnapshot | None = None,
):
    return analyze_paired_outcomes(
        rows,
        **_authority_kwargs(
            authority_rows or rows,
            snapshot=snapshot,
        ),
    )


def _estimate(
    rows: list[dict[str, object]],
    *,
    authority_rows: list[dict[str, object]] | None = None,
):
    return estimate_pilot(
        rows,
        **_authority_kwargs(authority_rows or rows),
    )


def test_paired_analysis_rejects_aliases_of_the_same_underlying_task() -> None:
    rows = [
        {
            **_row("alias-one", b_pass=True, c_pass=False),
            "task_identity": _task_identity("issue-42"),
        },
        {
            **_row("alias-two", b_pass=False, c_pass=True),
            "task_identity": {
                **_task_identity("ISSUE-42"),
                "repo": "git@github.com:unity-technologies/example",
                "canonical_repo_id": (
                    "git@github.com:unity-technologies/example"
                ),
            },
        },
    ]

    with pytest.raises(ValueError, match="aliased canonical|canonical task"):
        _analyze(rows)


def test_paired_analysis_rejects_alias_only_rows_without_canonical_identity() -> None:
    with pytest.raises(ValueError, match="task_identity"):
        _analyze(
            [{"task_id": "alias-only", "b_pass": True, "c_pass": False}],
            authority_rows=[
                _row("alias-only", b_pass=True, c_pass=False)
            ],
        )


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

    analysis = _analyze(list(rows))

    assert analysis.n11 == 20
    assert analysis.n10 == 12
    assert analysis.n01 == 3
    assert analysis.n00 == 15
    assert analysis.discordant_pairs == 15
    assert analysis.risk_difference == pytest.approx((12 - 3) / 50)
    assert 0.0 <= analysis.mcnemar_exact_p_value <= 1.0
    assert analysis.newcombe_ci95[0] < analysis.risk_difference
    assert analysis.newcombe_ci95[1] > analysis.risk_difference


def test_paired_analysis_requires_an_authoritative_snapshot() -> None:
    rows = [_row("task-1", b_pass=True, c_pass=False)]

    with pytest.raises(ValueError, match="authoritative ITT snapshot"):
        analyze_paired_outcomes(
            rows,
            experiment_id=_EXPERIMENT_ID,
            authoritative_snapshot_resolver=lambda _experiment_id: None,
        )


def test_paired_analysis_reconciles_the_complete_authoritative_itt_roster() -> None:
    task_one = _row("task-1", b_pass=True, c_pass=False)
    task_two = _row("task-2", b_pass=False, c_pass=True)

    with pytest.raises(ValueError, match="omitted=task-2"):
        _analyze([task_one], authority_rows=[task_one, task_two])

    with pytest.raises(ValueError, match="not in authoritative assignments"):
        _analyze([task_one, task_two], authority_rows=[task_one])


def test_paired_analysis_rejects_unterminated_or_mismatched_outcomes() -> None:
    authoritative_row = _row("task-1", b_pass=True, c_pass=False)
    unterminated = _snapshot(
        [authoritative_row],
        status_by_task_arm={("task-1", "B"): "running"},
    )
    with pytest.raises(ValueError, match="unterminated"):
        _analyze([authoritative_row], snapshot=unterminated)

    forged_row = {
        **authoritative_row,
        "b_pass": False,
    }
    with pytest.raises(ValueError, match="terminal outcome"):
        _analyze(
            [forged_row],
            authority_rows=[authoritative_row],
        )


def test_paired_analysis_rejects_duplicate_authoritative_assignments() -> None:
    row = _row("task-1", b_pass=True, c_pass=False)
    assignment = FrozenITTAssignment(
        task_id="task-1",
        canonical_task_id=canonical_task_identity(row["task_identity"]),
        assignment_id=str(row["assignment_id"]),
    )
    snapshot = FrozenITTSnapshot.create(
        experiment_id=_EXPERIMENT_ID,
        assignments=(assignment, assignment),
        terminal_outcomes=_snapshot([row]).terminal_outcomes,
        frozen_at_ms=1_720_000_000_000,
    )

    with pytest.raises(ValueError, match="duplicate authoritative assignment"):
        _analyze([row], snapshot=snapshot)


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
    task_id = (
        str(rows[0].get("task_id") or "task-1")
        if rows
        else "task-1"
    )
    authority_rows = [
        _row(task_id, b_pass=True, c_pass=False)
    ]
    with pytest.raises(ValueError, match=match):
        _analyze(rows, authority_rows=authority_rows)


def test_pilot_rejects_string_boolean_classifications() -> None:
    with pytest.raises(ValueError, match="verifier_flake must be a bool"):
        _estimate(
            [
                {
                    **_row("task-1", b_pass=True, c_pass=False),
                    "verifier_flake": "false",
                }
            ]
        )


def test_pilot_estimates_require_complete_authoritative_rows() -> None:
    def operating(row: dict[str, object]) -> dict[str, object]:
        return {
            **row,
            **{
                f"{metric}_{arm}": 1.0
                for metric in ("cost", "latency_ms", "risk_cost")
                for arm in ("a", "b", "c")
            },
        }

    task_one = operating(_row("pilot-1", b_pass=True, c_pass=False))
    task_two = operating(_row("pilot-2", b_pass=False, c_pass=True))

    with pytest.raises(ValueError, match="omitted=pilot-2"):
        _estimate([task_one], authority_rows=[task_one, task_two])


@pytest.mark.parametrize(
    "missing_field",
    ["cost_a", "latency_ms_b", "risk_cost_c"],
)
def test_pilot_requires_complete_finite_nonnegative_operating_data(
    missing_field: str,
) -> None:
    row = {
        **_row("task-1", b_pass=True, c_pass=False),
        **{
            f"{metric}_{arm}": 1.0
            for metric in ("cost", "latency_ms", "risk_cost")
            for arm in ("a", "b", "c")
        },
    }
    row.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        _estimate(
            [row],
            authority_rows=[
                {
                    **_row("task-1", b_pass=True, c_pass=False),
                    **{
                        f"{metric}_{arm}": 1.0
                        for metric in ("cost", "latency_ms", "risk_cost")
                        for arm in ("a", "b", "c")
                    },
                }
            ],
        )


def test_confirmation_size_is_frozen_from_pilot_discordance_not_attempt_count() -> None:
    pilot = PilotEstimate(
        task_count=80,
        discordant_task_count=16,
        verifier_flake_count=1,
        infrastructure_failure_count=2,
        mean_cost_by_arm={"A": 0.8, "B": 1.2, "C": 1.0},
        mean_latency_ms_by_arm={"A": 900.0, "B": 1200.0, "C": 1000.0},
        mean_risk_cost_by_arm={"A": 0.0, "B": 0.02, "C": 0.01},
        task_ids=tuple(f"pilot-{index}" for index in range(80)),
        canonical_task_ids=tuple(f"{index:064x}" for index in range(80)),
    )

    plan = derive_confirmation_plan(
        pilot,
        alternative_b_win_rate=0.65,
    )

    assert plan.required_discordant_pairs == 114
    assert plan.pilot_discordance_rate == pytest.approx(0.20)
    assert plan.conservative_discordance_rate < plan.pilot_discordance_rate
    assert plan.discordance_bound_method == "wilson-lower-95"
    assert plan.total_unique_tasks > 570
    assert plan.total_arm_runs == plan.total_unique_tasks * 3
    assert plan.plan_hash


def test_confirmation_plan_supports_minimum_preregistered_win_rate() -> None:
    pilot = PilotEstimate(
        task_count=80,
        discordant_task_count=16,
        verifier_flake_count=1,
        infrastructure_failure_count=2,
        mean_cost_by_arm={"A": 0.8, "B": 1.2, "C": 1.0},
        mean_latency_ms_by_arm={"A": 900.0, "B": 1200.0, "C": 1000.0},
        mean_risk_cost_by_arm={"A": 0.0, "B": 0.02, "C": 0.01},
        task_ids=tuple(f"pilot-{index}" for index in range(80)),
        canonical_task_ids=tuple(f"{index:064x}" for index in range(80)),
    )

    plan = derive_confirmation_plan(
        pilot,
        alternative_b_win_rate=0.55,
    )

    assert plan.required_discordant_pairs == 1055
    assert plan.total_unique_tasks >= plan.required_discordant_pairs
    assert plan.total_arm_runs == plan.total_unique_tasks * 3
    assert plan.plan_hash
    before = exact_discordant_pairs_required.cache_info().hits
    assert exact_discordant_pairs_required(
        win_rate=0.55,
        alpha=0.05,
        power=0.90,
    ) == 1055
    assert exact_discordant_pairs_required.cache_info().hits > before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"alternative_b_win_rate": 0.99},
        {"alternative_b_win_rate": 0.65, "power": 0.80},
    ],
)
def test_confirmation_plan_rejects_fantasy_alternatives_and_underpowered_designs(
    kwargs: dict[str, float],
) -> None:
    pilot = PilotEstimate(
        task_count=80,
        discordant_task_count=16,
        verifier_flake_count=0,
        infrastructure_failure_count=0,
        mean_cost_by_arm={"A": 1.0, "B": 1.0, "C": 1.0},
        mean_latency_ms_by_arm={"A": 1.0, "B": 1.0, "C": 1.0},
        mean_risk_cost_by_arm={"A": 0.0, "B": 0.0, "C": 0.0},
        task_ids=tuple(f"pilot-{index}" for index in range(80)),
        canonical_task_ids=tuple(f"{index:064x}" for index in range(80)),
    )

    with pytest.raises(ValueError, match="preregistered|power"):
        derive_confirmation_plan(pilot, **kwargs)


def test_pilot_estimate_rejects_incomplete_aggregate_operating_data() -> None:
    with pytest.raises(ValueError, match="mean_cost_by_arm"):
        PilotEstimate(
            task_count=2,
            discordant_task_count=1,
            verifier_flake_count=0,
            infrastructure_failure_count=0,
            mean_cost_by_arm={"B": 1.0, "C": 1.0},
            mean_latency_ms_by_arm={"A": 1.0, "B": 1.0, "C": 1.0},
            mean_risk_cost_by_arm={"A": 0.0, "B": 0.0, "C": 0.0},
            task_ids=("one", "two"),
            canonical_task_ids=("1" * 64, "2" * 64),
        )


def test_pilot_and_confirmation_task_sets_must_be_disjoint() -> None:
    require_disjoint_task_sets(
        [_task_identity("pilot-a")],
        [_task_identity("confirm-a")],
    )
    with pytest.raises(ValueError, match="canonical task overlap"):
        require_disjoint_task_sets(
            [_task_identity("shared")],
            [
                {
                    **_task_identity("SHARED"),
                    "repo": "git@github.com:unity-technologies/example",
                    "canonical_repo_id": (
                        "git@github.com:unity-technologies/example"
                    ),
                }
            ],
        )


def test_pilot_and_confirmation_sets_reject_internal_canonical_aliases() -> None:
    with pytest.raises(ValueError, match="pilot task set contains canonical"):
        require_disjoint_task_sets(
            [
                _task_identity("shared"),
                {
                    **_task_identity("SHARED"),
                    "repo": "git@github.com:unity-technologies/example",
                    "canonical_repo_id": (
                        "git@github.com:unity-technologies/example"
                    ),
                },
            ],
            [_task_identity("confirmation")],
        )


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


def test_roi_allows_a_cheaper_harness_with_negative_incremental_cost() -> None:
    roi = compute_harness_roi(
        task_count=100,
        success_b=0.55,
        success_c=0.50,
        cost_b=0.60,
        cost_c=1.00,
        latency_cost_per_task=0.05,
        expected_risk_cost_per_task=0.05,
        value_per_verified_success=10.0,
    )

    assert roi.incremental_successes == pytest.approx(5.0)
    assert roi.incremental_operating_cost == pytest.approx(-40.0)
    assert roi.cost_per_incremental_success == pytest.approx(-8.0)
    assert roi.classification == "harness_effect_positive_roi_positive"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_count", True),
        ("success_b", 1.01),
        ("success_c", -0.01),
        ("cost_b", -1.0),
        ("cost_c", math.nan),
        ("latency_cost_per_task", -0.01),
        ("expected_risk_cost_per_task", math.inf),
        ("value_per_verified_success", 0.0),
    ],
)
def test_roi_rejects_impossible_or_non_finite_inputs(
    field: str,
    value: object,
) -> None:
    inputs: dict[str, object] = {
        "task_count": 100,
        "success_b": 0.50,
        "success_c": 0.40,
        "cost_b": 1.0,
        "cost_c": 0.9,
        "latency_cost_per_task": 0.01,
        "expected_risk_cost_per_task": 0.01,
        "value_per_verified_success": 10.0,
    }
    inputs[field] = value

    with pytest.raises(ValueError, match=field):
        compute_harness_roi(**inputs)
