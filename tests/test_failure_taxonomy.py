from __future__ import annotations

from supervisor.failure_taxonomy import MAST_FAILURE_MODES, blocking_probe_failure, classify_failure
from supervisor.trace_envelope import stamp_trace_envelope, timed_tool_call


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
    assert receipt_gap["mast_code"] == "FM-3.2"
    assert receipt_gap["mast_mode"] == "No or incomplete verification"
    assert skill_gap["category"] == "governance"
    assert skill_gap["subcategory"] == "missing_skill_provenance"
    assert skill_gap["mast_code"] is None


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
    assert taxonomy["mast_code"] == "FM-1.1"


def test_failure_taxonomy_maps_all_mast_modes_without_losing_supervisor_fields():
    cases = {
        "disobey_task_spec": "FM-1.1",
        "role_violation_covenant": "FM-1.2",
        "repeated_gate_duplicate_round": "FM-1.3",
        "lost_conversation_history": "FM-1.4",
        "agents_not_converged_max_rounds": "FM-1.5",
        "conversation_reset": "FM-2.1",
        "ambiguous_input_no_clarification": "FM-2.2",
        "scope_violation_off_scope": "FM-2.3",
        "information_withholding": "FM-2.4",
        "objection_unaddressed_ignored_critique": "FM-2.5",
        "reasoning_action_mismatch": "FM-2.6",
        "premature_accept_probe_skipped": "FM-3.1",
        "workflow_claim_verification_failed": "FM-3.2",
        "false_green_incorrect_verification": "FM-3.3",
    }

    observed = {}
    for reason, mast_code in cases.items():
        taxonomy = classify_failure(reason=reason, source="test")
        observed[mast_code] = taxonomy
        assert taxonomy["mast_code"] == mast_code
        assert taxonomy["mast_mode"] == MAST_FAILURE_MODES[mast_code]["mode"]
        assert taxonomy["mast_category"] == MAST_FAILURE_MODES[mast_code]["category"]
        assert taxonomy["category"]
        assert taxonomy["subcategory"]
        assert taxonomy["code"] == reason

    assert set(observed) == set(MAST_FAILURE_MODES)
    internal = classify_failure(reason="lead_invocation_timeout", probe_id="P2")
    assert internal["category"] == "tool_execution"
    assert internal["mast_code"] is None
    assert internal["mast_mode"] is None


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
    assert payload["trace_envelope"]["failure_taxonomy"]["mast_code"] == "FM-3.2"


def test_trace_envelope_classifies_accepted_payload_with_missing_required_probe():
    payload = stamp_trace_envelope(
        run_id="run-1",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "accepted",
            "required_probes": ["P1", "P2", "P3", "CURSOR"],
            "probes": {
                "P1": {"probe_id": "P1", "status": "green", "reason": "ok"},
                "P2": {"probe_id": "P2", "status": "green", "reason": "ok"},
                "P3": {"probe_id": "P3", "status": "green", "reason": "ok"},
            },
        },
    )

    failure = payload["trace_envelope"]["failure_taxonomy"]
    assert payload["trace_envelope"]["policy_verdict"] == "blocked"
    assert failure["mast_code"] == "FM-3.1"
    assert failure["details"]["missing_probes"] == ["CURSOR"]


def test_timed_tool_call_stamps_wall_clock_and_monotonic_duration():
    wall_ms = [1_000]
    monotonic_ns = [10_000_000]

    def fake_wall_ms():
        return wall_ms[0]

    def fake_monotonic_ns():
        return monotonic_ns[0]

    with timed_tool_call(
        "invoke_claude_lead",
        wall_clock_ms=fake_wall_ms,
        monotonic_ns=fake_monotonic_ns,
        model="opus",
    ) as record:
        record["status"] = "completed"
        wall_ms[0] = 1_999
        monotonic_ns[0] = 145_000_000

    assert record["name"] == "invoke_claude_lead"
    assert record["model"] == "opus"
    assert record["started_at_ms"] == 1_000
    assert record["duration_ms"] == 135
    assert record["ended_at_ms"] == 1_135


def test_timed_tool_call_stamps_exception_metadata_before_reraising():
    record = None

    try:
        with timed_tool_call("explode") as call:
            record = call
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert record is not None
    assert record["name"] == "explode"
    assert record["status"] == "error"
    assert record["error"] == {
        "type": "RuntimeError",
        "message": "boom",
    }
    assert {"started_at_ms", "ended_at_ms", "duration_ms"} <= set(record)


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

    for envelope in (from_metadata["trace_envelope"], from_payload["trace_envelope"]):
        assert envelope["tool_calls"]
        for call in envelope["tool_calls"]:
            assert {"started_at_ms", "ended_at_ms", "duration_ms"} <= set(call)
            assert call["ended_at_ms"] >= call["started_at_ms"]
            assert call["duration_ms"] >= 0
    assert from_metadata["trace_envelope"]["tool_calls"][0]["name"] == "invoke_claude_lead"
    assert from_payload["trace_envelope"]["tool_calls"][0]["name"] == "start_dual_agent_gate"
