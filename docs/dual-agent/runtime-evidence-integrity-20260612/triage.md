# Triage: runtime-evidence-integrity-20260612

- run_id: `db0533b6-2e89-4deb-825d-6e249c74fa1c`
- task_id: `runtime-evidence-integrity-20260612`
- final_event_id: `735796`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `93`
- total_duration_ms: `4047359`
- total_duration_us: `4047380257`
- total_tokens_in: `17785218`
- total_tokens_out: `212144`
- total_cost_usd: `78.918353`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 735487 | start_dual_agent_gate#1781376333670#376588198 |  |  | start_dual_agent_gate | completed | 376588 | 376588198 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "implementation_plan", "min_subagents": 3, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "runtime-evidence-integrity-20260612", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 735486 | invoke_claude_lead#1781376333678#376566918 |  |  | invoke_claude_lead | completed | 376566 | 376566918 | 1373640 | 24847 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "implementation_plan", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "runtime-evidence-integrity-20260612", "timeout_s": 900} | {"cost_usd": 10.22227275, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 11448, "tokens_in": 1373640, "tokens_out": 24847} |  |
| 735796 | start_dual_agent_gate#1781377212467#365793320 |  |  | start_dual_agent_gate | completed | 365793 | 365793320 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "outcome_review", "min_subagents": 3, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "runtime-evidence-integrity-20260612", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 735795 | invoke_claude_lead#1781377212475#365763851 |  |  | invoke_claude_lead | completed | 365763 | 365763851 | 1364520 | 22465 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "outcome_review", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "runtime-evidence-integrity-20260612", "timeout_s": 900} | {"cost_usd": 8.1458355, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 11824, "tokens_in": 1364520, "tokens_out": 22465} |  |
| 735167 | start_dual_agent_gate#1781375770346#345918673 |  |  | start_dual_agent_gate | completed | 345918 | 345918673 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "tdd_review", "min_subagents": 3, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "runtime-evidence-integrity-20260612", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |

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

Inspect the latest gate result and replay manifest before advancing.
