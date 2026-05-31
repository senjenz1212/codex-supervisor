from __future__ import annotations

from supervisor.dual_agent_lead import DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES


def _complete_dynamic_receipts() -> list[dict]:
    shared = {
        "subagents": [
            {
                "task_id": "workflow-1:audit-1",
                "persona_id": "reviewer.codebase_audit",
                "timeout_s": 300,
                "budget_usd": 1.5,
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed", "pytest --collect-only"],
                "transcript_ref": "artifacts/dynamic/audit-1/transcript.jsonl",
                "output_hash": "sha256:abc123",
                "changed_files": [],
            }
        ],
        "supervision_layer": "codex_plus_lead",
        "lead_integrator": "claude_code_lead",
        "output_schema": "dynamic-workflow-output/v1",
        "output_hash": "sha256:abc123",
        "headless": True,
        "no_session_persistence": True,
        "replay_ref": "docs/dual-agent/workflow-1/replay/manifest.json",
        "worktree_comparison_ref": "docs/dual-agent/workflow-1/dynamic-comparison.md",
    }
    return [
        {
            "receipt_id": f"dyn-{gate}",
            "kind": "dynamic_workflow_receipt",
            "status": "passed",
            "gate": gate,
            **shared,
        }
        for gate in DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES
    ]


def test_verify_dynamic_workflow_receipts_rejects_missing_preview_gates():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[],
    )

    assert probe.probe_id == "P13"
    assert probe.status == "red"
    assert probe.reason == "missing_dynamic_workflow_receipts"
    assert set(probe.details["missing_gates"]) == set(DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES)


def test_verify_dynamic_workflow_receipts_accepts_complete_preview_manifest():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=_complete_dynamic_receipts(),
    )

    assert probe.probe_id == "P13"
    assert probe.status == "green"
    assert probe.reason == "dynamic_workflow_receipts_verified"
    assert probe.details["verified_gates"] == list(DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES)


def test_verify_dynamic_workflow_receipts_rejects_names_only_manifest():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic-workflow-preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[
            {
                "receipt_id": "dyn-names-only",
                "kind": "dynamic_workflow_manifest",
                "status": "passed",
                "gates": {gate: {} for gate in DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES},
            }
        ],
    )

    assert probe.probe_id == "P13"
    assert probe.status == "red"
    assert probe.reason == "missing_dynamic_workflow_receipts"
    assert set(probe.details["missing_gates"]) == set(DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES)
    assert probe.details["invalid_gates"]


def test_verify_dynamic_workflow_receipts_skips_lead_direct():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[],
    )

    assert probe.probe_id == "P13"
    assert probe.status == "green"
    assert probe.reason == "dynamic_workflow_not_requested"
