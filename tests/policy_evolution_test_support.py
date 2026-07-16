from __future__ import annotations

from typing import Any, Mapping

from supervisor.autoresearch.schema import sha256_json
from supervisor.state import State


def record_test_policy_proposal(
    state: State,
    *,
    run_id: str,
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    """Record a ledger-valid proposal for downstream approval tests."""
    claim_gate = {
        "schema_version": "supervisor-claim-gate/v1",
        "max_claim_level": "L3",
        "evidence_bundle_sha256": "1" * 64,
    }
    report_sha256 = "2" * 64
    report_event_id = state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_report_emitted",
        payload={
            "schema_version": "supervisor-autoresearch/v1",
            "report_sha256": report_sha256,
            "claim_gate": claim_gate,
        },
    )
    [report_event] = [
        event
        for event in state.read_events_since(
            run_id,
            after_event_id=0,
            limit=10,
        )
        if int(event["event_id"]) == report_event_id
    ]
    recorded = {
        "schema_version": "supervisor-autoresearch-policy-proposal/v1",
        "status": "draft",
        "source": "autoresearch",
        "requires_operator_approval": True,
        "operator_approved": False,
        "default_change_allowed": False,
        "automatic_policy_mutation": False,
        "gate_advanced": False,
        "gate_authority": "unchanged",
        "reviewer_panel_authority": "unchanged",
        "typed_outcome_authority": "unchanged",
        **dict(proposal),
        "claim_authority": {
            "schema_version": "supervisor-policy-claim-authority/v1",
            "report_sha256": report_sha256,
            "claim_gate_schema_version": claim_gate["schema_version"],
            "max_claim_level": claim_gate["max_claim_level"],
            "evidence_bundle_sha256": claim_gate[
                "evidence_bundle_sha256"
            ],
            "report_event_id": report_event_id,
            "report_event_hash": report_event["event_hash"],
        },
    }
    recorded["proposal_sha256"] = sha256_json(recorded)
    proposal_event_id = state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload=recorded,
    )
    return {
        **recorded,
        "proposal_event_id": proposal_event_id,
    }
