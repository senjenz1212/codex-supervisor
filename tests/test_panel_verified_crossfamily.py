from __future__ import annotations

from supervisor.cursor_agent import CursorInvocationResult
from supervisor.dual_agent import Outcome, ProbeResult
from supervisor.mergeability_bench import _reviewer_cross_family_claim_status
from supervisor.reviewer_registry import (
    ReviewerSpec,
    evaluate_reviewer_panel,
    independent_reviewer_results_from_review_results,
)
from supervisor.swe_bench_mergeability import (
    ARM_BASELINE,
    ARM_ORACLE_CEILING,
    ARM_S_FULL,
    ARM_S_PROBE,
    _build_official_all_arms_diagnostic_report,
)


def _outcome(decision: str = "accept") -> Outcome:
    return Outcome(
        task_id="panel-verified-crossfamily",
        summary="fixture reviewer outcome",
        specialists=[{"name": "Independent Reviewer", "decision": decision}],
        decisions=[decision],
        objections=[] if decision == "accept" else ["contaminated objection"],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=0.9,
        confidence_rationale="fixture",
        confidence_criteria=[],
        claims=[],
        critical_review={
            "decision": decision,
            "severity": "none" if decision == "accept" else "critical",
        },
    )


def _cursor_result(
    *,
    model: str | None,
    runtime: str = "litellm_structured",
    decision: str = "accept",
) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult("INDEPENDENT_REVIEWER", "green", "fixture_ok", {}),
        outcome=_outcome(decision),
        transcript=f"fixture transcript from {model}",
        status="finished",
        model=model,
        reviewer_runtime=runtime,
        reviewer_output_mode=runtime,
        reviewer_assurance="structured_text_only",
    )


def _panel_input(
    reviewer_id: str,
    decision: str,
    *,
    confidence: float = 0.95,
    severity: str = "none",
) -> dict:
    return {
        "reviewer_id": reviewer_id,
        "verdict_present": True,
        "decision": decision,
        "accepted": decision == "accept",
        "severity": severity,
        "confidence": confidence,
        "runtime": "litellm_structured",
        "model": f"gemini-{reviewer_id}",
        "provider_family": "google",
        "provider_family_verified": True,
        "provider_family_source": "served_model",
    }


def _arm_summary() -> dict:
    return {
        "availability_status": "available",
        "unavailable_count": 0,
        "false_accept_rate": 0.0,
        "true_accept_rate": 1.0,
    }


def _reviewer(
    reviewer_id: str,
    provider_family: str,
    model: str,
) -> dict:
    return {
        "reviewer_id": reviewer_id,
        "runtime": "litellm_structured",
        "reviewer_runtime": "litellm_structured",
        "model": model,
        "provider_family": provider_family,
        "provider_family_verified": True,
        "provider_family_source": "served_model",
        "verdict_present": True,
        "decision": "accept",
        "accepted": True,
        "severity": "none",
        "tool_access": "text_only",
        "assurance_grade": "text_only",
        "transcript_sha256": "a" * 64,
        "output_sha256": "b" * 64,
    }


def _official_report(*, panel_aggregation_mode: str) -> dict:
    return {
        "status": "completed",
        "dataset": "swe-bench-pro",
        "dataset_split": "test",
        "report_path": "official_replay_report.json",
        "report_sha256": "f" * 64,
        "oracle_adapter_kind": "official_docker_or_equivalent",
        "instance_count": 1,
        "candidate_count": 1,
        "configured_reviewer_panel_preflight": {"full_roster_available": True},
        "hidden_field_leak_check": {"ok": True},
        "replay_report": {
            "instance_reports": [
                {
                    "independent_reviewer_results": [
                        {
                            "instance_id": "task-1",
                            "candidate_id": "candidate-1",
                            "panel_decision": {
                                "schema_version": "independent-reviewer-panel-decision/v1",
                                "decision": "accept",
                                "reason": "fixture",
                                "aggregation_mode": panel_aggregation_mode,
                                "available_reviewers": ["google-reviewer", "anthropic-reviewer"],
                                "accepted_reviewers": ["google-reviewer", "anthropic-reviewer"],
                                "blocking_reviewers": [],
                                "non_accepting_reviewers": [],
                                "missing_reviewers": [],
                            },
                            "reviewer_results": [
                                _reviewer(
                                    "google-reviewer",
                                    "google",
                                    "gemini-3.1-pro-preview",
                                ),
                                _reviewer(
                                    "anthropic-reviewer",
                                    "anthropic",
                                    "claude-opus-4-8",
                                ),
                            ],
                        }
                    ]
                }
            ]
        },
        "bridge_report": {
            "arms": {
                ARM_BASELINE: _arm_summary(),
                ARM_S_PROBE: _arm_summary(),
                ARM_S_FULL: _arm_summary(),
                ARM_ORACLE_CEILING: _arm_summary(),
            },
            "per_row_results": [
                {
                    "instance_id": "task-1",
                    "candidate_id": "candidate-1",
                    "baseline_candidate_artifact_hash": "candidate-hash",
                    "baseline_producer": {
                        "provider_family": "openai",
                        "provider": "openai",
                        "model": "gpt-5.5-generator",
                    },
                    "s_probe_accept": True,
                    "oracle_accept": True,
                    "oracle_unavailable": False,
                }
            ],
            "false_accept_at_matched_true_accept": {
                ARM_S_FULL: {"status": "computed"},
            },
        },
    }


def test_provider_family_from_served_model_not_flag():
    spec = ReviewerSpec(
        reviewer_id="independent-reviewer-litellm",
        runtime="litellm_structured",
        model="proxy-default",
        provider_family="google",
        lineage=("google", "litellm_structured", "proxy-default"),
        tool_access="text_only",
        assurance_grade="text_only",
    )

    rows = independent_reviewer_results_from_review_results(
        [(spec, _cursor_result(model="claude-3-5-sonnet-20260620"))],
        task_id="panel-verified-crossfamily",
        gate="mergeability_full_gate",
        round_index=0,
    )

    assert rows[0]["model"] == "claude-3-5-sonnet-20260620"
    assert rows[0]["provider_family"] == "anthropic"
    assert rows[0]["provider_family_verified"] is True
    assert rows[0]["provider_family_source"] == "served_model"
    assert _reviewer_cross_family_claim_status(rows[0]) == (
        "verified_served_provider_family",
        True,
    )


def test_operator_flag_alone_is_not_proven():
    spec = ReviewerSpec(
        reviewer_id="independent-reviewer-litellm",
        runtime="litellm_structured",
        model="proxy-default",
        provider_family="google",
        lineage=("google", "litellm_structured", "proxy-default"),
        tool_access="text_only",
        assurance_grade="text_only",
    )

    rows = independent_reviewer_results_from_review_results(
        [(spec, _cursor_result(model="proxy-default"))],
        task_id="panel-verified-crossfamily",
        gate="mergeability_full_gate",
        round_index=0,
    )

    assert rows[0]["provider_family"] == "google"
    assert rows[0]["provider_family_verified"] is False
    assert rows[0]["provider_family_source"] == "operator_config"
    assert _reviewer_cross_family_claim_status(rows[0]) == (
        "operator_asserted_provider_family_unverified",
        False,
    )


def test_requested_model_without_served_model_is_not_proven():
    spec = ReviewerSpec(
        reviewer_id="independent-reviewer-litellm",
        runtime="litellm_structured",
        model="gemini-3.1-pro-preview",
        provider_family="google",
        lineage=("google", "litellm_structured", "gemini-3.1-pro-preview"),
        tool_access="text_only",
        assurance_grade="text_only",
    )

    rows = independent_reviewer_results_from_review_results(
        [(spec, _cursor_result(model=None))],
        task_id="panel-verified-crossfamily",
        gate="mergeability_full_gate",
        round_index=0,
    )

    assert rows[0]["model"] is None
    assert rows[0]["requested_model"] == "gemini-3.1-pro-preview"
    assert rows[0]["provider_family"] == "google"
    assert rows[0]["provider_family_verified"] is False
    assert rows[0]["provider_family_source"] == "operator_config"
    assert _reviewer_cross_family_claim_status(rows[0]) == (
        "operator_asserted_provider_family_unverified",
        False,
    )


def test_robust_aggregation_bounds_one_contaminated_judge():
    decision = evaluate_reviewer_panel(
        [
            _panel_input("google-reviewer", "accept"),
            _panel_input("anthropic-reviewer", "accept"),
            _panel_input(
                "contaminated-reviewer",
                "deny",
                confidence=1.0,
                severity="low",
            ),
        ],
        aggregation_mode="geometric_median",
    )

    assert decision["aggregation_mode"] == "geometric_median"
    assert decision["decision"] == "accept"
    assert decision["reason"] == "robust_geometric_median_accept"
    assert decision["non_accepting_reviewers"] == ["contaminated-reviewer"]
    robust = decision["robust_aggregation"]
    assert robust["geometric_median_score"] == 1.0
    assert robust["score_points"] == [1.0, 1.0, 0.0]
    assert robust["contaminated_judge_bound"] == 1


def test_unverified_panel_blocks_measurement():
    report = _build_official_all_arms_diagnostic_report(
        official_report=_official_report(panel_aggregation_mode="conservative"),
        min_good=1,
        min_bad=0,
    )

    verification = report["reviewer_roster_cross_family_verification"]
    assert verification["status"] == "blocked"
    assert "reviewer_panel_not_robustly_aggregated" in verification["reasons"]
    assert verification["non_robust_panel_results"]
    assert report["all_arms_populated"] is False
    assert report["status"] == "unavailable"
    assert "reviewer_panel_not_robustly_aggregated" in report["metrics_unavailable_reasons"]
    assert report["leave_one_reviewer_out"]["status"] == "unavailable"
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["policy_mutated"] is False
