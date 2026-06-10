# Triage: supervisor-trend-metrics-20260610

- run_id: `95563d35-0618-41b5-872f-5dd8d9ef6047`
- task_id: `supervisor-trend-metrics-20260610`
- final_event_id: `649661`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `157`
- total_duration_ms: `4011299`
- total_duration_us: `4011333311`
- total_tokens_in: `21265542`
- total_tokens_out: `186810`
- total_cost_usd: `85.713501`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 649852 | invoke_cursor_agent#1781122746746#252272882 |  |  | invoke_cursor_agent | finished | 252272 | 252272882 |  |  |  | ["to_prd-supervisor-trend-metrics-20260610", "prd_grill-supervisor-trend-metrics-20260610", "to_issues-supervisor-trend-metrics-20260610", "tdd-supervisor-trend-metrics-20260610", "tdd_grill-supervisor-trend-metrics-20260610", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 13, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "supervisor-trend-metrics-20260610", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 649853 | invoke_cursor_agent#1781122746746#252272882 |  |  | invoke_cursor_agent | finished | 252272 | 252272882 |  |  |  | ["to_prd-supervisor-trend-metrics-20260610", "prd_grill-supervisor-trend-metrics-20260610", "to_issues-supervisor-trend-metrics-20260610", "tdd-supervisor-trend-metrics-20260610", "tdd_grill-supervisor-trend-metrics-20260610", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 13, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "supervisor-trend-metrics-20260610", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 649854 | invoke_cursor_agent#1781122746746#252272882 |  |  | invoke_cursor_agent | finished | 252272 | 252272882 |  |  |  | ["to_prd-supervisor-trend-metrics-20260610", "prd_grill-supervisor-trend-metrics-20260610", "to_issues-supervisor-trend-metrics-20260610", "tdd-supervisor-trend-metrics-20260610", "tdd_grill-supervisor-trend-metrics-20260610", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 13, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "supervisor-trend-metrics-20260610", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 648819 | start_dual_agent_gate#1781120756303#198049598 |  |  | start_dual_agent_gate | completed | 198049 | 198049598 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": "supervisor_trend_metrics", "execution_layer_mode": "lead_direct", "gate": "tdd_review", "min_subagents": 3, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "supervisor-trend-metrics-20260610", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 648818 | invoke_claude_lead#1781120756308#198030584 |  |  | invoke_claude_lead | completed | 198030 | 198030584 | 1194005 | 14230 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": "supervisor_trend_metrics", "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "tdd_review", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "supervisor-trend-metrics-20260610", "timeout_s": 900} | {"cost_usd": 3.890175, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 8940, "tokens_in": 1194005, "tokens_out": 14230} |  |

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
