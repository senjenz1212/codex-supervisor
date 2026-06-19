# Triage: mergeability-heldout-corpus-no-regression-20260619

- run_id: `07c585f2-68b8-4ad3-bbf1-f25541673ec1`
- task_id: `mergeability-heldout-corpus-no-regression-20260619`
- final_event_id: `818124`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `229`
- total_duration_ms: `8840018`
- total_duration_us: `8840064970`
- total_tokens_in: `42887084`
- total_tokens_out: `329008`
- total_cost_usd: `143.952354`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 817477 | start_dual_agent_gate#1781907699253#900062833 |  |  | start_dual_agent_gate | completed | 900062 | 900062833 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "execution", "min_subagents": 3, "planning_artifact_count": 12, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "mergeability-heldout-corpus-no-regression-20260619", "user_facing": false} | {"claude_gate_status": "blocked", "probe_statuses": {"P1": "green", "P2": "red", "P3": "red", "P4": "red", "P_planning": "green"}, "supervisor_final_status": "blocked"} |  |
| 817476 | invoke_claude_lead#1781907699261#900042846 |  |  | invoke_claude_lead | failed | 900042 | 900042846 |  |  | P2 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "execution", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "mergeability-heldout-corpus-no-regression-20260619", "timeout_s": 900} | {"cost_usd": null, "model": "opus", "outcome_present": false, "probe_id": "P2", "probe_reason": "lead_invocation_timeout", "probe_status": "red", "stderr_bytes": 0, "stdout_bytes": 0, "tokens_in": null, "tokens_out": null} | lead_invocation_timeout |
| 817897 | start_dual_agent_gate#1781909234348#886469886 |  |  | start_dual_agent_gate | completed | 886469 | 886469886 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "execution", "min_subagents": 3, "planning_artifact_count": 12, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "mergeability-heldout-corpus-no-regression-20260619", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 817896 | invoke_claude_lead#1781909234359#886443629 |  |  | invoke_claude_lead | completed | 886443 | 886443629 | 3796927 | 10545 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "execution", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "mergeability-heldout-corpus-no-regression-20260619", "timeout_s": 900} | {"cost_usd": 4.964219999999999, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 8559, "tokens_in": 3796927, "tokens_out": 10545} |  |
| 818296 | invoke_cursor_agent#1781911382506#275952978 |  |  | invoke_cursor_agent | finished | 275952 | 275952978 |  |  |  | ["skill-to-prd-mergeability-heldout-corpus-no-regression-20260619", "skill-prd-grill-mergeability-heldout-corpus-no-regression-20260619", "skill-to-issues-mergeability-heldout-corpus-no-regression-20260619", "skill-tdd-mergeability-heldout-corpus-no-regression-20260619", "skill-tdd-grill-mergeability-heldout-corpus-no-regression-20260619", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1", "runtime-tdd-coverage-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 12, "quality": "best", "receipt_count": 10, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "mergeability-heldout-corpus-no-regression-20260619", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |

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
