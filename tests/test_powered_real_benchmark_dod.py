from __future__ import annotations

import pytest

from supervisor.powered_real_benchmark import (
    PoweredRealBenchmarkDoDError,
    assert_powered_real_benchmark_definition_of_done,
)


AUTHORITY_FLAGS = {
    "metric_applyable": False,
    "improvement_claim_allowed": False,
    "powered_improvement_claim_allowed": False,
    "human_mergeability_claim_allowed": False,
    "default_change_allowed": False,
    "policy_mutated": False,
    "gate_advanced": False,
}


def _ci(*, count: int, denominator: int) -> dict[str, object]:
    return {
        "method": "wilson_score",
        "confidence": 0.95,
        "label": "approximate_binary_proportion_interval",
        "count": count,
        "denominator": denominator,
        "lower": 0.1,
        "upper": 0.3,
    }


def _powered_report() -> dict[str, object]:
    return {
        "schema_version": "supervisor-powered-factorial-mergeability-report/v1",
        "report_label": "powered_factorial_evaluation",
        "source_predictions_path": (
            "/mnt/pro-runs/artifacts/pro-predictions-real-20260628.jsonl"
        ),
        "pro_candidate_count": 120,
        "candidate_count": 120,
        "source_disclosure_counts": {
            "vacuous_pass_to_pass_count": 2,
            "rc_nonzero_resolved_count": 1,
        },
        "sample_size_sufficiency": {
            "status": "sufficient",
            "n_good": 45,
            "n_bad": 75,
            "min_good": 30,
            "min_bad": 30,
            "false_accept_denominator": 75,
            "true_accept_denominator": 45,
        },
        "paired_discordant_counts": {
            "full_supervisor_stack": {
                "left_arm": "single_agent_baseline",
                "right_arm": "full_supervisor_stack",
                "candidate_count": 120,
                "left_accept_right_reject": 25,
                "left_reject_right_accept": 0,
                "discordant_pair_count": 25,
                "concordant_pair_count": 95,
            }
        },
        "paired_power": {
            "status": "sufficient",
            "required_comparison": "full_supervisor_stack",
            "min_discordant": 25,
            "alpha": 0.05,
            "comparisons": {
                "full_supervisor_stack": {
                    "status": "sufficient",
                    "method": "mcnemar_chi_square_continuity_corrected",
                    "discordant_pair_count": 25,
                    "min_discordant": 25,
                    "alpha": 0.05,
                    "p_value": 0.0001,
                    "mcnemar_test_passed": True,
                }
            },
        },
        "evidence_conversion_power_contract": {
            "schema_version": "supervisor-evidence-conversion-power-contract/v1",
            "status": "qualified",
            "thresholds": {
                "min_good": 30,
                "min_bad": 30,
                "min_discordant": 25,
                "alpha": 0.05,
            },
            "sample_size_status": "sufficient",
            "paired_power_status": "sufficient",
            "required_comparison": {
                "status": "sufficient",
                "discordant_pair_count": 25,
                "p_value": 0.0001,
                "mcnemar_test_passed": True,
            },
            "reasons": [],
            "report_only": True,
            "operator_review_required": True,
            "policy_mutation_allowed": False,
        },
        "powered_metric_applyable": True,
        "promotion_guardrails": {
            "powered_threshold_required": True,
            "sample_size_threshold_met": True,
            "paired_power_threshold_met": True,
            "powered_threshold_met": True,
            "policy_mutation_allowed": False,
        },
        "recommendation": {
            "report_only": True,
            "applyable_policy_proposal": False,
            "operator_approval_required_for_any_policy_change": True,
        },
        **AUTHORITY_FLAGS,
    }


def _far_tar_frr_arm(*, n_bad: int = 75, n_good: int = 45) -> dict[str, object]:
    return {
        "false_accept_rate": 0.133333,
        "true_accept_rate": 0.8,
        "false_reject_rate": 0.2,
        "n_bad": n_bad,
        "n_good": n_good,
        "false_accept_confidence_interval": _ci(count=10, denominator=n_bad),
        "true_accept_confidence_interval": _ci(count=36, denominator=n_good),
        "false_reject_confidence_interval": _ci(count=9, denominator=n_good),
    }


def _all_arms_report() -> dict[str, object]:
    return {
        "schema_version": "supervisor-official-all-arms-diagnostic-report/v1",
        "status": "completed",
        "candidate_count": 120,
        "instance_count": 90,
        "n_good": 45,
        "n_bad": 75,
        "all_arms_populated": True,
        "diagnostic_ready_for_scale": True,
        "benchmark_oracle": {
            "kind": "swe_bench_held_out_test_pass_proxy",
            "scoring_authority": "held_out_fail_to_pass_and_pass_to_pass_tests",
            "maintainer_mergeability_claim_allowed": False,
            "limitation_note": (
                "SWE-bench reports held-out test-pass behavior; it is not a "
                "maintainer would-merge judgment."
            ),
        },
        "swe_bench_oracle_limitation_note": (
            "SWE-bench held-out tests are a test-pass proxy, not human "
            "maintainer mergeability."
        ),
        "no_maintainer_mergeability_claim": True,
        "far_tar_frr": {
            "baseline": _far_tar_frr_arm(),
            "s_probe": _far_tar_frr_arm(),
            "s_full": _far_tar_frr_arm(),
            "oracle_ceiling": _far_tar_frr_arm(),
        },
        "reviewer_marginal_delta_at_matched_true_accept": {
            "status": "computed",
            "false_accept_rate_delta": -0.2,
            "matched_true_accept_rate": 0.8,
        },
        "aeb0_artifact_gate": {
            "authority_flags": dict(AUTHORITY_FLAGS),
        },
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
        },
        **AUTHORITY_FLAGS,
    }


def test_artifact_is_powered():
    verdict = assert_powered_real_benchmark_definition_of_done(
        powered_report=_powered_report(),
        all_arms_diagnostic_report=_all_arms_report(),
    )

    assert verdict["definition_of_done_met"] is True
    assert verdict["status"] == "passed"
    assert verdict["evidence"]["n_good"] == 45
    assert verdict["evidence"]["n_bad"] == 75
    assert verdict["evidence"]["discordant_pair_count"] == 25
    assert verdict["evidence"]["mcnemar_p_value"] == 0.0001


def test_artifact_far_and_wilson_present():
    verdict = assert_powered_real_benchmark_definition_of_done(
        powered_report=_powered_report(),
        all_arms_diagnostic_report=_all_arms_report(),
    )

    assert verdict["evidence"]["far_tar_frr_arms"] == [
        "baseline",
        "oracle_ceiling",
        "s_full",
        "s_probe",
    ]
    for ci_path in verdict["evidence"]["confidence_interval_paths"]:
        assert ci_path.endswith("_confidence_interval")


def test_artifact_is_report_only_test_pass_proxy():
    verdict = assert_powered_real_benchmark_definition_of_done(
        powered_report=_powered_report(),
        all_arms_diagnostic_report=_all_arms_report(),
    )

    assert verdict["evidence"]["benchmark_oracle_kind"] == (
        "swe_bench_held_out_test_pass_proxy"
    )
    assert verdict["authority_flags"] == AUTHORITY_FLAGS
    assert verdict["evidence"]["real_benchmark_claim_allowed"] is True
    assert verdict["evidence"]["policy_mutation_allowed"] is False


def test_report_discloses_vacuous_and_rc_nonzero_counts():
    verdict = assert_powered_real_benchmark_definition_of_done(
        powered_report=_powered_report(),
        all_arms_diagnostic_report=_all_arms_report(),
    )

    assert verdict["evidence"]["vacuous_pass_to_pass_count"] == 2
    assert verdict["evidence"]["rc_nonzero_resolved_count"] == 1


def test_report_requires_source_disclosure_counts():
    powered = _powered_report()
    powered.pop("source_disclosure_counts")

    with pytest.raises(
        PoweredRealBenchmarkDoDError,
        match="source_disclosure_counts_missing",
    ):
        assert_powered_real_benchmark_definition_of_done(
            powered_report=powered,
            all_arms_diagnostic_report=_all_arms_report(),
        )


def test_underpowered_artifact_is_rejected():
    powered = _powered_report()
    sample_size = powered["sample_size_sufficiency"]
    assert isinstance(sample_size, dict)
    sample_size["n_bad"] = 0
    sample_size["status"] = "underpowered"

    with pytest.raises(
        PoweredRealBenchmarkDoDError,
        match="sample_size_sufficiency_status_underpowered",
    ):
        assert_powered_real_benchmark_definition_of_done(
            powered_report=powered,
            all_arms_diagnostic_report=_all_arms_report(),
        )
