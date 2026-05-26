"""Review-only SP-N stability proposals derived from probe cohorts."""
from __future__ import annotations

from typing import Any


PROPOSED_CHANGES: dict[str, str] = {
    "FM-1.2": "Add covenant compliance checks before accepting reviewer decisions.",
    "FM-1.3": "Reject repeated gate executions with identical handoff, gate, and round hashes.",
    "FM-2.5": "Enforce addresses-graph completeness for objections across rounds.",
    "FM-3.1": "Require every mandatory probe to be present before a gate can accept.",
    "FM-3.2": "Require receipt-backed verification for implementation and test claims.",
    "FM-3.3": "Require independent cross-check evidence when verifiers disagree.",
}


def stability_proposals_for_cohort(cohort_summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate deterministic, non-mutating stability proposals for review."""
    counts = cohort_summary.get("failure_counts_by_mast_code")
    if not isinstance(counts, dict) or not counts:
        return []
    trials = cohort_summary.get("trials") if isinstance(cohort_summary.get("trials"), list) else []
    evidence_refs = _evidence_refs(trials)
    proposals: list[dict[str, Any]] = []
    for index, mast_code in enumerate(sorted(str(code) for code in counts), start=1):
        proposals.append({
            "schema_version": "dual-agent-stability-proposal/v1",
            "proposal_id": f"SP-{index:03d}",
            "status": "proposed",
            "requires_human": True,
            "cohort_id": str(cohort_summary.get("cohort_id") or ""),
            "classification": str(cohort_summary.get("classification") or ""),
            "trigger_metric": {
                "mast_code": mast_code,
                "observed_count": int(counts.get(mast_code) or 0),
            },
            "mast_code": mast_code,
            "evidence_refs": evidence_refs,
            "proposed_change": PROPOSED_CHANGES.get(
                mast_code,
                f"Define a stability remediation for {mast_code}.",
            ),
            "risk": "operator_review_required_before_policy_change",
            "validation_criteria": [
                "cohort re-run remains STABLE",
                "no prior passing receipt replay invariant regresses",
                f"{mast_code} has at least one deterministic regression test",
            ],
            "non_action_guarantee": "proposal_only_no_runtime_policy_change",
        })
    return proposals


def _evidence_refs(trials: list[Any]) -> list[str]:
    refs: list[str] = []
    for trial in trials:
        if not isinstance(trial, dict):
            continue
        ref = str(trial.get("manifest_path") or "").strip()
        if not ref:
            artifact_dir = str(trial.get("artifact_dir") or "").strip()
            ref = f"{artifact_dir}/replay/manifest.json" if artifact_dir else ""
        if ref:
            refs.append(ref)
    return refs
