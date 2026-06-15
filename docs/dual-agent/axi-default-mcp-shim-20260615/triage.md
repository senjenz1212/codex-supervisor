# Triage: axi-default-mcp-shim-20260615

- run_id: `2e2f2f31-868f-42f2-9b03-621c76946575`
- task_id: `axi-default-mcp-shim-20260615`
- final_event_id: `766439`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `177`
- total_duration_ms: `6592510`
- total_duration_us: `6592544899`
- total_tokens_in: `32570658`
- total_tokens_out: `225764`
- total_cost_usd: `109.675041`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 766189 | start_dual_agent_gate#1781504962101#900035120 |  |  | start_dual_agent_gate | completed | 900035 | 900035120 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "execution", "min_subagents": 3, "planning_artifact_count": 7, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "axi-default-mcp-shim-20260615", "user_facing": false} | {"claude_gate_status": "blocked", "probe_statuses": {"P1": "green", "P2": "red", "P3": "red", "P4": "red", "P_planning": "green"}, "supervisor_final_status": "blocked"} |  |
| 766188 | invoke_claude_lead#1781504962108#900021551 |  |  | invoke_claude_lead | failed | 900021 | 900021551 |  |  | P2 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "execution", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "axi-default-mcp-shim-20260615", "timeout_s": 900} | {"cost_usd": null, "model": "opus", "outcome_present": false, "probe_id": "P2", "probe_reason": "lead_invocation_timeout", "probe_status": "red", "stderr_bytes": 0, "stdout_bytes": 0, "tokens_in": null, "tokens_out": null} | lead_invocation_timeout |
| 766667 | invoke_cursor_agent#1781506947666#324636414 |  |  | invoke_cursor_agent | finished | 324636 | 324636414 |  |  |  | ["axi-default-mcp-shim-20260615-to_prd-source-r2", "axi-default-mcp-shim-20260615-prd_grill-source-r2", "axi-default-mcp-shim-20260615-to_issues-source-r2", "axi-default-mcp-shim-20260615-tdd-source-r2", "axi-default-mcp-shim-20260615-tdd_grill-source-r2", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-tdd-coverage-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1", "runtime-tdd-coverage-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 7, "quality": "best", "receipt_count": 15, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "axi-default-mcp-shim-20260615", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 766668 | invoke_cursor_agent#1781506947666#324636414 |  |  | invoke_cursor_agent | finished | 324636 | 324636414 |  |  |  | ["axi-default-mcp-shim-20260615-to_prd-source-r2", "axi-default-mcp-shim-20260615-prd_grill-source-r2", "axi-default-mcp-shim-20260615-to_issues-source-r2", "axi-default-mcp-shim-20260615-tdd-source-r2", "axi-default-mcp-shim-20260615-tdd_grill-source-r2", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-tdd-coverage-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1", "runtime-tdd-coverage-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 7, "quality": "best", "receipt_count": 15, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "axi-default-mcp-shim-20260615", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 766669 | invoke_cursor_agent#1781506947666#324636414 |  |  | invoke_cursor_agent | finished | 324636 | 324636414 |  |  |  | ["axi-default-mcp-shim-20260615-to_prd-source-r2", "axi-default-mcp-shim-20260615-prd_grill-source-r2", "axi-default-mcp-shim-20260615-to_issues-source-r2", "axi-default-mcp-shim-20260615-tdd-source-r2", "axi-default-mcp-shim-20260615-tdd_grill-source-r2", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-tdd-coverage-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1", "runtime-tdd-coverage-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 7, "quality": "best", "receipt_count": 15, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "axi-default-mcp-shim-20260615", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |

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
