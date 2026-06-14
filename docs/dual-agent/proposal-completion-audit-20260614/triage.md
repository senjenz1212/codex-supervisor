# Triage: proposal-completion-audit-20260614

- run_id: `8d7acabb-3d30-4bcc-8296-b65b7c99c8b4`
- task_id: `proposal-completion-audit-20260614`
- final_event_id: `746864`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `158`
- total_duration_ms: `5023320`
- total_duration_us: `5023349940`
- total_tokens_in: `48507116`
- total_tokens_out: `253044`
- total_cost_usd: `102.501445`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 745697 | start_dual_agent_gate#1781414319318#446976429 |  |  | start_dual_agent_gate | completed | 446976 | 446976429 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "execution", "min_subagents": 3, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "proposal-completion-audit-20260614", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 745696 | invoke_claude_lead#1781414319326#446954936 |  |  | invoke_claude_lead | completed | 446954 | 446954936 | 7711342 | 25652 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "execution", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "proposal-completion-audit-20260614", "timeout_s": 900} | {"cost_usd": 6.702446, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 9212, "tokens_in": 7711342, "tokens_out": 25652} |  |
| 746664 | start_dual_agent_gate#1781415526905#378887746 |  |  | start_dual_agent_gate | completed | 378887 | 378887746 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "outcome_review", "min_subagents": 3, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "proposal-completion-audit-20260614", "user_facing": false} | {"claude_gate_status": "blocked", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "red", "P_planning": "green"}, "supervisor_final_status": "blocked"} |  |
| 746663 | invoke_claude_lead#1781415526915#378867384 |  |  | invoke_claude_lead | completed | 378867 | 378867384 | 2850834 | 23088 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "outcome_review", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "proposal-completion-audit-20260614", "timeout_s": 900} | {"cost_usd": 10.249233, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 13619, "tokens_in": 2850834, "tokens_out": 23088} |  |
| 747262 | invoke_cursor_agent#1781416061844#378741518 |  |  | invoke_cursor_agent | finished | 378741 | 378741518 |  |  |  | ["skill-to-prd-proposal-completion-audit-20260614", "skill-prd-grill-proposal-completion-audit-20260614", "skill-to-issues-proposal-completion-audit-20260614", "skill-tdd-proposal-completion-audit-20260614", "skill-tdd-grill-proposal-completion-audit-20260614", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-tdd-coverage-execution-1", "runtime-baseline-execution-2", "runtime-git-diff-execution-2", "runtime-deliverables-execution-2", "runtime-tests-execution-2", "runtime-tdd-coverage-execution-2", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1", "runtime-tdd-coverage-outcome_review-1", "runtime-baseline-outcome_review-3", "runtime-git-diff-outcome_review-3", "runtime-deliverables-outcome_review-3", "runtime-tests-outcome_review-3", "runtime-tdd-coverage-outcome_review-3"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 25, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "proposal-completion-audit-20260614", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |

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
