# Triage: cursor-review-reliability-20260531

- run_id: `codex-cursor-review-reliability-20260531-after-implementation`
- task_id: `cursor-review-reliability-20260531`
- final_event_id: `310751`
- policy_verdict: `blocked`
- claude_gate_status: `blocked`
- supervisor_final_status: `blocked`

## Run Totals

- unique_tool_calls: `46`
- total_duration_ms: `826563`
- total_duration_us: `826572919`
- total_tokens_in: `6122930`
- total_tokens_out: `57438`
- total_cost_usd: `24.034379`

## Root Cause

- failure_code: `reviewer_contract_unmet`
- failure_category: `system_design`
- failure_subcategory: `unknown`
- mast_code: ``
- mast_mode: ``

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 310736 | start_dual_agent_gate#1780258322962#169239382 |  |  | start_dual_agent_gate | completed | 169239 | 169239382 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "tdd_review", "min_subagents": 0, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "cursor-review-reliability-20260531", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 310735 | invoke_claude_lead#1780258322970#169218656 |  |  | invoke_claude_lead | completed | 169218 | 169218656 | 1464731 | 11799 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "tdd_review", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "cursor-review-reliability-20260531", "timeout_s": 900} | {"cost_usd": 4.100183250000001, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 8632, "tokens_in": 1464731, "tokens_out": 11799} |  |
| 310693 | start_dual_agent_gate#1780258079619#137539451 |  |  | start_dual_agent_gate | completed | 137539 | 137539451 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "prd_review", "min_subagents": 0, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "cursor-review-reliability-20260531", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 310692 | invoke_claude_lead#1780258079627#137515730 |  |  | invoke_claude_lead | completed | 137515 | 137515730 | 1061857 | 9300 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "prd_review", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "cursor-review-reliability-20260531", "timeout_s": 900} | {"cost_usd": 4.6035825, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 10009, "tokens_in": 1061857, "tokens_out": 9300} |  |
| 310704 | start_dual_agent_gate#1780258219105#101897756 |  |  | start_dual_agent_gate | completed | 101897 | 101897756 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "issues_review", "min_subagents": 0, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "cursor-review-reliability-20260531", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |

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
