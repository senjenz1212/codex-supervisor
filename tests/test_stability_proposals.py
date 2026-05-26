from __future__ import annotations

from supervisor.stability_proposals import stability_proposals_for_cohort


def test_stability_proposals_are_review_only_for_repeated_failures():
    proposals = stability_proposals_for_cohort({
        "cohort_id": "cohort-1",
        "classification": "STABLE",
        "failure_counts_by_mast_code": {"FM-3.2": 3},
        "trials": [
            {
                "trial_id": "trial-1",
                "artifact_dir": "docs/dual-agent/trial-1",
                "manifest_path": "docs/dual-agent/trial-1/replay/manifest.json",
            },
            {
                "trial_id": "trial-2",
                "artifact_dir": "docs/dual-agent/trial-2",
                "manifest_path": "docs/dual-agent/trial-2/replay/manifest.json",
            },
        ],
    })

    assert [proposal["proposal_id"] for proposal in proposals] == ["SP-001"]
    assert proposals[0]["status"] == "proposed"
    assert proposals[0]["requires_human"] is True
    assert proposals[0]["mast_code"] == "FM-3.2"
    assert proposals[0]["non_action_guarantee"] == "proposal_only_no_runtime_policy_change"
    assert "applied" not in proposals[0].values()
    assert proposals[0]["evidence_refs"] == [
        "docs/dual-agent/trial-1/replay/manifest.json",
        "docs/dual-agent/trial-2/replay/manifest.json",
    ]


def test_stability_proposals_skip_cohorts_without_failures():
    assert stability_proposals_for_cohort({
        "cohort_id": "cohort-1",
        "classification": "STABLE",
        "failure_counts_by_mast_code": {},
        "trials": [],
    }) == []


def test_stability_proposals_are_deterministic_and_sorted():
    proposals = stability_proposals_for_cohort({
        "cohort_id": "cohort-1",
        "classification": "FLIPPING",
        "failure_counts_by_mast_code": {"FM-3.3": 1, "FM-1.3": 2},
        "trials": [{"trial_id": "trial-1", "artifact_dir": "docs/dual-agent/trial-1"}],
    })

    assert [(p["proposal_id"], p["mast_code"]) for p in proposals] == [
        ("SP-001", "FM-1.3"),
        ("SP-002", "FM-3.3"),
    ]
