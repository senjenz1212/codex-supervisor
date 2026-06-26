from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.autoresearch.policy_evolution import (
    derive_policy_evolution_proposals_from_report,
)
from supervisor.mergeability_bench import (
    build_mergeability_corpus_manifest,
    run_powered_factorial_mergeability_evaluation,
)


BENCH_ROOT = Path("tests/fixtures/mergeability_bench")


def _candidate_hashes() -> dict[str, str]:
    manifest = build_mergeability_corpus_manifest(BENCH_ROOT)
    return {
        str(entry["candidate_id"]): str(entry["candidate_hash"])
        for entry in manifest["candidates"]
    }


def _produced_baseline_decisions(
    *,
    accepted_ids: set[str],
) -> dict[str, dict[str, object]]:
    return {
        candidate_id: {
            "candidate_id": candidate_id,
            "accept": candidate_id in accepted_ids,
            "candidate_artifact_hash": candidate_hash,
            "decision_source": "produced_single_agent_baseline",
            "producer": {
                "agent": "discordant-power-fixture",
                "runner_label": "discordant-power-fixture",
                "model": "fixture-baseline-llm",
            },
            "prompt_sha256": sha256(f"baseline:{candidate_id}".encode("utf-8")).hexdigest(),
        }
        for candidate_id, candidate_hash in _candidate_hashes().items()
    }


def _arm_decisions(
    *,
    baseline_accepts: set[str],
    full_stack_accepts: set[str],
) -> dict[str, dict[str, object]]:
    candidate_ids = set(_candidate_hashes())
    return {
        "single_agent_baseline": _produced_baseline_decisions(
            accepted_ids=baseline_accepts,
        ),
        "full_supervisor_stack": {
            candidate_id: candidate_id in full_stack_accepts
            for candidate_id in candidate_ids
        },
    }


def _report_for_accept_sets(
    tmp_path: Path,
    *,
    baseline_accepts: set[str],
    full_stack_accepts: set[str],
    min_discordant: int = 25,
) -> dict[str, object]:
    return run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_arm_decisions(
            baseline_accepts=baseline_accepts,
            full_stack_accepts=full_stack_accepts,
        ),
        reviewer_panel_results={},
        powered_thresholds={
            "min_bad": 1,
            "min_good": 1,
            "min_discordant": min_discordant,
        },
    )


def test_all_concordant_rows_report_underpowered(tmp_path):
    candidate_ids = set(_candidate_hashes())
    report = _report_for_accept_sets(
        tmp_path,
        baseline_accepts=candidate_ids,
        full_stack_accepts=candidate_ids,
    )

    comparison = report["paired_power"]["comparisons"]["full_supervisor_stack"]
    assert report["sample_size_sufficiency"]["status"] == "sufficient"
    assert comparison["discordant_pair_count"] == 0
    assert comparison["status"] == "underpowered"
    assert report["paired_power"]["status"] == "underpowered"
    assert report["promotion_guardrails"]["sample_size_threshold_met"] is True
    assert report["promotion_guardrails"]["paired_power_threshold_met"] is False
    assert report["promotion_guardrails"]["powered_threshold_met"] is False
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False
    assert report["promotion_guardrails"]["policy_mutation_allowed"] is False
    assert report["recommendation"]["applyable_policy_proposal"] is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=Path.cwd(),
        affected_gates=("execution", "outcome_review"),
    ) == []


def test_exact_binomial_used_under_25_discordant(tmp_path):
    candidate_ids = sorted(_candidate_hashes())
    baseline_accepts = set(candidate_ids)
    full_stack_accepts = set(candidate_ids[1:])
    report = _report_for_accept_sets(
        tmp_path,
        baseline_accepts=baseline_accepts,
        full_stack_accepts=full_stack_accepts,
    )

    comparison = report["paired_power"]["comparisons"]["full_supervisor_stack"]
    assert report["sample_size_sufficiency"]["status"] == "sufficient"
    assert comparison["discordant_pair_count"] == 1
    assert comparison["method"] == "exact_binomial_two_sided"
    assert comparison["p_value"] == 1.0
    assert comparison["status"] == "underpowered"
    assert report["paired_power"]["status"] == "underpowered"
    assert report["metric_applyable"] is False


def test_continuity_corrected_mcnemar_used_at_threshold(tmp_path):
    candidate_ids = sorted(_candidate_hashes())
    baseline_accepts = set(candidate_ids)
    full_stack_accepts = set(candidate_ids[10:])
    report = _report_for_accept_sets(
        tmp_path,
        baseline_accepts=baseline_accepts,
        full_stack_accepts=full_stack_accepts,
        min_discordant=10,
    )

    comparison = report["paired_power"]["comparisons"]["full_supervisor_stack"]
    assert comparison["discordant_pair_count"] == 10
    assert comparison["method"] == "mcnemar_chi_square_continuity_corrected"
    assert comparison["statistic"] == pytest.approx(8.1)
    assert comparison["p_value"] < comparison["alpha"]
    assert comparison["status"] == "sufficient"
    assert report["paired_power"]["status"] == "sufficient"
    assert report["metric_applyable"] is True


def test_far_zero_reports_rule_of_three_upper_bound(tmp_path):
    candidate_ids = set(_candidate_hashes())
    report = _report_for_accept_sets(
        tmp_path,
        baseline_accepts=candidate_ids,
        full_stack_accepts=set(),
    )

    full_stack = report["arms"]["full_supervisor_stack"]
    oracle = report["arms"]["oracle_ceiling"]
    zero_intervals = [
        full_stack["false_accept_confidence_interval"],
        full_stack["true_accept_confidence_interval"],
        oracle["false_reject_confidence_interval"],
    ]
    for interval in zero_intervals:
        assert interval["method"] == "rule_of_three"
        assert interval["lower"] == 0.0
        assert interval["upper"] == pytest.approx(
            min(1.0, 3 / interval["denominator"]),
            abs=0.000001,
        )


def test_rates_carry_wilson_intervals(tmp_path):
    candidate_ids = sorted(_candidate_hashes())
    report = _report_for_accept_sets(
        tmp_path,
        baseline_accepts=set(candidate_ids[:10]),
        full_stack_accepts=set(candidate_ids[5:15]),
        min_discordant=1,
    )

    for arm in report["arms"].values():
        for prefix in ("false_accept", "true_accept", "false_reject"):
            summary = arm[f"{prefix}_rate_summary"]
            interval = arm[f"{prefix}_confidence_interval"]
            assert set(summary) >= {
                "rate",
                "ci_low",
                "ci_high",
                "confidence_interval",
            }
            assert summary["ci_low"] == interval["lower"]
            assert summary["ci_high"] == interval["upper"]
            assert interval["method"] in {"wilson_score", "rule_of_three"}
