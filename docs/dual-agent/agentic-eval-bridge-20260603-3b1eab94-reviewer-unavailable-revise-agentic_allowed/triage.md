# Triage: agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed

- run_id: `agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed-5efe3d8cc9fe`
- task_id: `agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed`
- final_event_id: `462517`
- policy_verdict: `blocked`
- claude_gate_status: `blocked`
- supervisor_final_status: `blocked`

## Run Totals

- unique_tool_calls: `7`
- total_duration_ms: `113749`
- total_duration_us: `113752400`
- total_tokens_in: `0`
- total_tokens_out: `0`
- total_cost_usd: `0.0`

## Root Cause

- failure_code: `planning_validation_failed`
- failure_category: `system_design`
- failure_subcategory: `artifact_quality`
- mast_code: `FM-1.1`
- mast_mode: `Disobey task specification`

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 462509 | produce_agentic_worker_receipts#1780503323524#113744988 |  |  | produce_agentic_worker_receipts | passed | 113744 | 113744988 |  |  |  | [] | {"agentic_lead_policy": "allowed", "existing_receipt_count": 0, "min_subagents": 1, "required_roles": [], "run_id": "agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed-5efe3d8cc9fe", "task_id": "agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed"} | {"blocking_findings": [], "receipt_count": 1, "status": "passed"} |  |
| 462517 | start_dual_agent_gate#1780503438696#5567 |  |  | start_dual_agent_gate | completed | 5 | 5567 |  |  |  |  | {"agentic_lead_policy": "allowed", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "prd_review", "min_subagents": 1, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed", "user_facing": false} | {"claude_gate_status": "blocked", "probe_statuses": {"P_planning": "red"}, "supervisor_final_status": "blocked"} |  |
| 462512 | verify_dynamic_workflow_receipts#1780503437272#534 |  |  | verify_dynamic_workflow_receipts | green | 0 | 534 |  |  | P13 | ["agentic-worker-audit-1"] | {"agentic_lead_policy": "allowed", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "min_subagents": 1, "receipt_count": 1, "required_evidence_grade": "self_reported", "required_roles": [], "task_id": "agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed"} | {"missing_gates": [], "probe_id": "P13", "reason": "dynamic_workflow_not_requested", "status": "green", "verified_gates": []} |  |
| 462514 | verify_dynamic_workflow_receipts#1780503438693#497 |  |  | verify_dynamic_workflow_receipts | green | 0 | 497 |  |  | P13 | ["agentic-worker-audit-1"] | {"agentic_lead_policy": "allowed", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "prd_review", "min_subagents": 1, "receipt_count": 1, "required_evidence_grade": "self_reported", "required_roles": [], "task_id": "agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed"} | {"agentic_policy_status": "accepted", "missing_gates": [], "probe_id": "P13", "reason": "dynamic_workflow_not_requested", "status": "green", "verified_gates": []} |  |
| 462515 | validate_planning_artifacts#1780503438698#814 |  |  | validate_planning_artifacts | red | 0 | 814 |  |  | P_planning |  | {"artifact_count": 6, "gate": "prd_review", "required_kinds": ["prd"], "task_id": "agentic-eval-bridge-20260603-3b1eab94-reviewer-unavailable-revise-agentic_allowed"} | {"probe_id": "P_planning", "reason": "planning_validation_failed", "status": "red"} | planning_validation_failed |

## Evidence Pointers

- [Interactions](interactions.md)
- [Transcript](transcript.md)
- [Machine Transcript](transcript.jsonl)
- [MAST Coverage](mast-coverage.md)
- [Replay Manifest](replay/manifest.json)
- [Source PRD](source/prd.md)
- [Source PRD Grill Findings](source/grill-findings.md)
- [Source Issues](source/issues.md)
- [Source TDD](source/tdd.md)
- [Source TDD Grill Findings](source/grill-findings-tdd.md)
- [Source Implementation Plan](source/implementation-plan.md)

## Next Safe Action

Inspect the failure event, resolve the named taxonomy blocker, then rerun the blocked gate.
