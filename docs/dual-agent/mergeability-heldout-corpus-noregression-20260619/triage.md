# Triage: mergeability-heldout-corpus-noregression-20260619

- run_id: `8f516566-f059-4270-8613-6fd7c48f48b6`
- task_id: `mergeability-heldout-corpus-noregression-20260619`
- final_event_id: `820491`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `153`
- total_duration_ms: `8323750`
- total_duration_us: `8323777665`
- total_tokens_in: `34245542`
- total_tokens_out: `195576`
- total_cost_usd: `93.971559`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 820011 | start_dual_agent_gate#1781915233120#900063075 |  |  | start_dual_agent_gate | completed | 900063 | 900063075 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "execution", "min_subagents": 3, "planning_artifact_count": 11, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "mergeability-heldout-corpus-noregression-20260619", "user_facing": false} | {"claude_gate_status": "blocked", "probe_statuses": {"P1": "green", "P2": "red", "P3": "red", "P4": "red", "P_planning": "green"}, "supervisor_final_status": "blocked"} |  |
| 820010 | invoke_claude_lead#1781915233128#900038115 |  |  | invoke_claude_lead | failed | 900038 | 900038115 |  |  | P2 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "execution", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "mergeability-heldout-corpus-noregression-20260619", "timeout_s": 900} | {"cost_usd": null, "model": "opus", "outcome_present": false, "probe_id": "P2", "probe_reason": "lead_invocation_timeout", "probe_status": "red", "stderr_bytes": 0, "stdout_bytes": 0, "tokens_in": null, "tokens_out": null} | lead_invocation_timeout |
| 820248 | start_dual_agent_gate#1781917173361#718160046 |  |  | start_dual_agent_gate | completed | 718160 | 718160046 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "execution", "min_subagents": 3, "planning_artifact_count": 11, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "mergeability-heldout-corpus-noregression-20260619", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 820247 | invoke_claude_lead#1781917173370#718135924 |  |  | invoke_claude_lead | completed | 718135 | 718135924 | 2414048 | 7917 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "execution", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "mergeability-heldout-corpus-noregression-20260619", "timeout_s": 900} | {"cost_usd": 3.0973069999999994, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 7022, "tokens_in": 2414048, "tokens_out": 7917} |  |
| 820195 | start_dual_agent_gate#1781916421307#715752044 |  |  | start_dual_agent_gate | completed | 715752 | 715752044 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "execution", "min_subagents": 3, "planning_artifact_count": 11, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "mergeability-heldout-corpus-noregression-20260619", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |

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
