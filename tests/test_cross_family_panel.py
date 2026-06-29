from __future__ import annotations

from supervisor.cursor_agent import CursorInvocationRequest, CursorInvocationResult
from supervisor.dual_agent import Outcome, ProbeResult
from supervisor.mergeability_bench import (
    ConfiguredReviewerPanelOptions,
    SUPERVISOR_CONFIGURED_PANEL_LITELLM_MODEL_DEFAULT,
    SUPERVISOR_CONFIGURED_PANEL_LITELLM_PROVIDER_FAMILY_DEFAULT,
)
from supervisor.reviewer_registry import (
    configured_reviewers,
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
        task_id="cross-family-panel",
        summary="fixture reviewer outcome",
        specialists=[{"name": "Independent Reviewer", "decision": decision}],
        decisions=[decision],
        objections=[] if decision == "accept" else ["fixture objection"],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=0.9,
        confidence_rationale="fixture",
        confidence_criteria=[],
        claims=[],
        critical_review={
            "decision": decision,
            "severity": "none" if decision == "accept" else "important",
        },
    )


def _cursor_result(request: CursorInvocationRequest, decision: str = "accept") -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult("INDEPENDENT_REVIEWER", "green", "fixture_ok", {}),
        outcome=_outcome(decision),
        transcript="fixture transcript",
        status="finished",
        model=request.reviewer_model,
        reviewer_runtime=request.reviewer_output_mode,
        reviewer_output_mode=request.reviewer_output_mode,
        reviewer_assurance="structured_text_only",
    )


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
    *,
    runtime: str = "litellm_structured",
    model: str = "fixture-model",
    decision: str = "accept",
) -> dict:
    return {
        "reviewer_id": reviewer_id,
        "runtime": runtime,
        "reviewer_runtime": runtime,
        "model": model,
        "provider_family": provider_family,
        "verdict_present": True,
        "decision": decision,
        "accepted": decision == "accept",
        "severity": "none" if decision == "accept" else "important",
        "tool_access": "text_only" if runtime == "litellm_structured" else "codebase_tools",
        "assurance_grade": "text_only" if runtime == "litellm_structured" else "agentic",
        "transcript_sha256": "a" * 64,
        "output_sha256": "b" * 64,
    }


def _official_report(
    *,
    baseline_family: str,
    reviewer_results: list[dict],
) -> dict:
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
                            "reviewer_results": reviewer_results,
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
                        "provider_family": baseline_family,
                        "provider": baseline_family,
                        "model": f"{baseline_family}-generator",
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


def test_litellm_reviewer_reports_real_provider_family(tmp_path):
    captured: list[CursorInvocationRequest] = []

    def litellm_runner(request: CursorInvocationRequest) -> CursorInvocationResult:
        captured.append(request)
        return _cursor_result(request)

    reviewers = configured_reviewers(
        reviewer_output_mode="cursor_sdk",
        reviewer_model=None,
        litellm_runner=litellm_runner,
        litellm_model="proxy-default",
        litellm_provider_family="google",
    )
    litellm_reviewer = next(
        reviewer
        for reviewer in reviewers
        if reviewer.spec.reviewer_id == "independent-reviewer-litellm"
    )

    result = litellm_reviewer.review(
        CursorInvocationRequest(
            task_id="cross-family-panel",
            gate=ConfiguredReviewerPanelOptions().gate,
            instruction="Review.",
            cwd=tmp_path,
        )
    )
    rows = independent_reviewer_results_from_review_results(
        [(litellm_reviewer.spec, result)],
        task_id="cross-family-panel",
        gate=ConfiguredReviewerPanelOptions().gate,
        round_index=0,
    )

    assert captured
    assert captured[0].reviewer_output_mode == "litellm_structured"
    assert captured[0].reviewer_model == "proxy-default"
    assert rows[0]["provider_family"] == "google"
    assert rows[0]["provider_family"] != "openai_compatible"
    assert rows[0]["tool_access"] == "text_only"
    assert rows[0]["assurance_grade"] == "text_only"


def test_litellm_reviewer_is_opt_in_to_avoid_silent_roster_expansion():
    assert SUPERVISOR_CONFIGURED_PANEL_LITELLM_MODEL_DEFAULT == ""
    assert SUPERVISOR_CONFIGURED_PANEL_LITELLM_PROVIDER_FAMILY_DEFAULT == ""

    default_reviewers = configured_reviewers(
        reviewer_output_mode="cursor_sdk",
        reviewer_model=None,
    )
    default_ids = {reviewer.spec.reviewer_id for reviewer in default_reviewers}
    assert "independent-reviewer-litellm" not in default_ids

    explicit_reviewers = configured_reviewers(
        reviewer_output_mode="cursor_sdk",
        reviewer_model=None,
        litellm_model="gemini-3.1-pro-preview",
        litellm_provider_family="google",
    )
    explicit_litellm = [
        reviewer
        for reviewer in explicit_reviewers
        if reviewer.spec.reviewer_id == "independent-reviewer-litellm"
    ]
    assert len(explicit_litellm) == 1
    assert explicit_litellm[0].spec.provider_family == "google"


def test_same_family_panel_blocks_independence_measurement():
    report = _build_official_all_arms_diagnostic_report(
        official_report=_official_report(
            baseline_family="google",
            reviewer_results=[
                _reviewer("google-reviewer", "google", model="gemini-3.1-pro-preview"),
                _reviewer("anthropic-reviewer", "anthropic", model="claude-opus-4-8"),
            ],
        ),
        min_good=1,
        min_bad=0,
    )

    verification = report["reviewer_roster_cross_family_verification"]
    assert verification["status"] == "blocked"
    assert verification["reason"] == "reviewer_roster_not_verified_cross_family"
    assert verification["proven_provider_families"] == ["anthropic", "google"]
    assert verification["baseline_family_conflicts"]
    assert report["s_full_available"] is True
    assert report["all_arms_populated"] is False
    assert report["status"] == "unavailable"
    assert "reviewer_roster_not_verified_cross_family" in report["metrics_unavailable_reasons"]
    assert "same_family_generator_reviewer_decisive_vote" in report["metrics_unavailable_reasons"]
    assert "same_family_generator_reviewer_decisive_vote" in verification["reasons"]
    assert report["leave_one_reviewer_out"]["status"] == "unavailable"
    assert report["leave_one_reviewer_out"]["reason"] == "reviewer_roster_not_verified_cross_family"


def test_all_arms_unavailable_when_roster_unverified_cross_family():
    report = _build_official_all_arms_diagnostic_report(
        official_report=_official_report(
            baseline_family="anthropic",
            reviewer_results=[
                _reviewer(
                    "openai-reviewer",
                    "openai",
                    runtime="codex_cli",
                    model="gpt-5.5",
                ),
                _reviewer(
                    "proxy-reviewer",
                    "openai_compatible",
                    model="proxy-default",
                ),
            ],
        ),
        min_good=1,
        min_bad=0,
    )

    verification = report["reviewer_roster_cross_family_verification"]
    assert verification["status"] == "blocked"
    assert verification["unproven_reviewers"]
    assert verification["proven_provider_families"] == ["openai"]
    assert "unproven_provider_family" in verification["reasons"]
    assert "reviewer_roster_not_verified_cross_family" in report["metrics_unavailable_reasons"]
    assert "unproven_provider_family" in report["metrics_unavailable_reasons"]
    assert report["baseline_available"] is True
    assert report["s_probe_available"] is True
    assert report["s_full_available"] is True
    assert report["oracle_ceiling_available"] is True
    assert report["all_arms_populated"] is False
    assert report["diagnostic_ready_for_scale"] is False
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["policy_mutated"] is False
