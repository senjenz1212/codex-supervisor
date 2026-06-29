from __future__ import annotations

from supervisor.swe_bench_mergeability import (
    ARM_BASELINE,
    ARM_ORACLE_CEILING,
    ARM_S_FULL,
    ARM_S_PROBE,
    _build_official_all_arms_diagnostic_report,
)


def _arm_summary(*, false_accept_rate: float = 0.0, true_accept_rate: float = 1.0) -> dict:
    return {
        "availability_status": "available",
        "unavailable_count": 0,
        "false_accept_rate": false_accept_rate,
        "true_accept_rate": true_accept_rate,
    }


def _reviewer_result(reviewer_id: str, decision: str, *, runtime: str, model: str) -> dict:
    return {
        "reviewer_id": reviewer_id,
        "runtime": runtime,
        "model": model,
        "verdict_present": True,
        "available": True,
        "decision": decision,
        "transcript_sha256": "a" * 64,
        "output_sha256": "b" * 64,
    }


def _panel_decision(reviewers: list[dict]) -> dict:
    reviewer_ids = [str(item["reviewer_id"]) for item in reviewers]
    accepted = [
        str(item["reviewer_id"])
        for item in reviewers
        if str(item.get("decision")) == "accept"
    ]
    return {
        "schema_version": "independent-reviewer-panel-decision/v1",
        "decision": "accept" if len(accepted) == len(reviewer_ids) else "revise",
        "reason": "fixture",
        "aggregation_mode": "geometric_median",
        "available_reviewers": reviewer_ids,
        "accepted_reviewers": accepted,
        "blocking_reviewers": [],
        "non_accepting_reviewers": [
            reviewer_id for reviewer_id in reviewer_ids if reviewer_id not in accepted
        ],
        "missing_reviewers": [],
    }


def _official_report(*, include_reviewers: bool, zero_reviewer_errors: bool = False) -> dict:
    good_reviewers = [
        _reviewer_result("codex-reviewer", "accept", runtime="codex_cli", model="gpt-5.5"),
        _reviewer_result(
            "google-reviewer",
            "accept",
            runtime="litellm_structured",
            model="gemini-3.1-pro-preview",
        ),
    ]
    bad_reviewers = [
        _reviewer_result(
            "codex-reviewer",
            "deny" if zero_reviewer_errors else "accept",
            runtime="codex_cli",
            model="gpt-5.5",
        ),
        _reviewer_result(
            "google-reviewer",
            "deny",
            runtime="litellm_structured",
            model="gemini-3.1-pro-preview",
        ),
    ]
    if not include_reviewers:
        good_reviewers = []
        bad_reviewers = []

    return {
        "status": "completed",
        "dataset": "swe-bench-pro",
        "dataset_split": "test",
        "report_path": "official_replay_report.json",
        "report_sha256": "f" * 64,
        "oracle_adapter_kind": "official_docker_or_equivalent",
        "instance_count": 2,
        "candidate_count": 2,
        "configured_reviewer_panel_preflight": {"full_roster_available": True},
        "hidden_field_leak_check": {"ok": True},
        "replay_report": {
            "instance_reports": [
                {
                    "independent_reviewer_results": [
                        {
                            "instance_id": "task-good",
                            "candidate_id": "candidate-shared",
                            "reviewer_results": good_reviewers,
                            "panel_decision": _panel_decision(good_reviewers),
                        }
                    ]
                },
                {
                    "independent_reviewer_results": [
                        {
                            "instance_id": "task-bad",
                            "candidate_id": "candidate-shared",
                            "reviewer_results": bad_reviewers,
                            "panel_decision": _panel_decision(bad_reviewers),
                        }
                    ]
                },
            ]
        },
        "bridge_report": {
            "arms": {
                ARM_BASELINE: _arm_summary(false_accept_rate=1.0),
                ARM_S_PROBE: _arm_summary(false_accept_rate=1.0),
                ARM_S_FULL: _arm_summary(false_accept_rate=0.0),
                ARM_ORACLE_CEILING: _arm_summary(false_accept_rate=0.0),
            },
            "per_row_results": [
                {
                    "instance_id": "task-good",
                    "candidate_id": "candidate-shared",
                    "baseline_candidate_artifact_hash": "good-hash",
                    "baseline_producer": {"provider_family": "anthropic"},
                    "s_probe_accept": True,
                    "oracle_accept": True,
                    "oracle_unavailable": False,
                },
                {
                    "instance_id": "task-bad",
                    "candidate_id": "candidate-shared",
                    "baseline_candidate_artifact_hash": "bad-hash",
                    "baseline_producer": {"provider_family": "anthropic"},
                    "s_probe_accept": True,
                    "oracle_accept": False,
                    "oracle_unavailable": False,
                },
            ],
            "false_accept_at_matched_true_accept": {
                ARM_S_FULL: {"status": "computed"},
            },
        },
    }


def test_all_arms_report_includes_independence_metrics():
    report = _build_official_all_arms_diagnostic_report(
        official_report=_official_report(include_reviewers=True),
        min_good=1,
        min_bad=1,
    )

    candidate_pool_sha256 = report["candidate_pool_sha256"]
    assert report["independence_metric_candidate_pool_sha256"] == {
        "inter_reviewer_agreement": candidate_pool_sha256,
        "leave_one_reviewer_out": candidate_pool_sha256,
        "effective_vote_estimate": candidate_pool_sha256,
    }
    assert report["inter_reviewer_agreement"] == [
        {
            "reviewer_pair": ["codex-reviewer", "google-reviewer"],
            "shared_candidate_count": 2,
            "agreement_count": 1,
            "agreement_rate": 0.5,
        }
    ]
    assert report["leave_one_reviewer_out"]["status"] == "computed"
    assert {
        effect["reviewer_id"]: effect["leave_one_out_false_accept_rate"]
        for effect in report["leave_one_reviewer_out"]["reviewer_effects"]
    } == {
        "codex-reviewer": 0.0,
        "google-reviewer": 1.0,
    }
    assert report["effective_vote_estimate"]["status"] == "computed"
    assert report["effective_vote_estimate"]["effective_vote_count_estimate"] == 2.0
    assert report["metric_applyable"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False

    no_reviewers = _build_official_all_arms_diagnostic_report(
        official_report=_official_report(include_reviewers=False),
        min_good=1,
        min_bad=1,
    )

    assert no_reviewers["inter_reviewer_agreement"]["status"] == "unavailable"
    assert no_reviewers["inter_reviewer_agreement"]["reason"] == (
        "reviewer_roster_not_verified_cross_family"
    )
    assert no_reviewers["leave_one_reviewer_out"]["status"] == "unavailable"
    assert no_reviewers["leave_one_reviewer_out"]["reason"] == (
        "reviewer_roster_not_verified_cross_family"
    )
    assert no_reviewers["effective_vote_estimate"]["status"] == "unavailable"
    assert no_reviewers["effective_vote_estimate"]["reason"] == (
        "reviewer_roster_not_verified_cross_family"
    )

    zero_errors = _build_official_all_arms_diagnostic_report(
        official_report=_official_report(
            include_reviewers=True,
            zero_reviewer_errors=True,
        ),
        min_good=1,
        min_bad=1,
    )

    assert zero_errors["inter_reviewer_agreement"]
    assert zero_errors["leave_one_reviewer_out"]["status"] == "computed"
    assert zero_errors["effective_vote_estimate"]["status"] == "unavailable"
    assert (
        zero_errors["effective_vote_estimate"]["reason"]
        == "zero_oracle_grounded_reviewer_errors"
    )
