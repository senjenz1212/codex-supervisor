from __future__ import annotations

from supervisor.failure_taxonomy import blocking_probe_failure, classify_failure
from supervisor.trace_envelope import stamp_trace_envelope


def test_failure_taxonomy_classifies_receipt_and_skill_gaps():
    receipt_gap = classify_failure(
        reason="workflow_claim_verification_failed",
        probe_id="P11",
        source="validation_probe",
    )
    skill_gap = classify_failure(
        reason="missing_prd_tdd_skill_receipts",
        probe_id="P12",
        source="workflow_start",
    )

    assert receipt_gap["category"] == "task_verification"
    assert receipt_gap["subcategory"] == "missing_or_stale_receipt"
    assert skill_gap["category"] == "governance"
    assert skill_gap["subcategory"] == "missing_skill_provenance"


def test_blocking_probe_failure_includes_planning_and_claim_probes_but_not_p4():
    taxonomy = blocking_probe_failure({
        "P4": {"probe_id": "P4", "status": "red", "reason": "paused_for_human"},
        "P_planning": {
            "probe_id": "P_planning",
            "status": "red",
            "reason": "planning_validation_failed",
        },
    })

    assert taxonomy is not None
    assert taxonomy["probe_id"] == "P_planning"
    assert taxonomy["category"] == "system_design"


def test_trace_envelope_stamps_dual_agent_payloads_without_wrapping_original_shape():
    payload = stamp_trace_envelope(
        run_id="run-1",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "blocked",
            "probes": {
                "P11": {
                    "probe_id": "P11",
                    "status": "red",
                    "reason": "workflow_claim_verification_failed",
                },
            },
        },
    )

    assert payload["task_id"] == "task-1"
    assert payload["trace_envelope"]["schema_version"] == "dual-agent-trace-envelope/v1"
    assert payload["trace_envelope"]["policy_verdict"] == "blocked"
    assert payload["trace_envelope"]["failure_taxonomy"]["category"] == "task_verification"


def test_trace_envelope_extracts_tool_calls_from_metadata_or_payload():
    from_metadata = stamp_trace_envelope(
        run_id="run-1",
        source="dual_agent",
        kind="dual_agent_interaction_message",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "metadata": {
                "tool_calls": [
                    {"name": "invoke_claude_lead", "status": "green"},
                ],
            },
        },
    )
    from_payload = stamp_trace_envelope(
        run_id="run-1",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "tool_calls": [
                {"name": "start_dual_agent_gate", "status": "blocked"},
            ],
        },
    )

    assert from_metadata["trace_envelope"]["tool_calls"] == [
        {"name": "invoke_claude_lead", "status": "green"},
    ]
    assert from_payload["trace_envelope"]["tool_calls"] == [
        {"name": "start_dual_agent_gate", "status": "blocked"},
    ]
