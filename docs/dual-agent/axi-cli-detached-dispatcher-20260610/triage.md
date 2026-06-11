# Triage: axi-cli-detached-dispatcher-20260610

- run_id: `7a82b985-29fb-4346-ae2e-78a24c330694`
- task_id: `axi-cli-detached-dispatcher-20260610`
- final_event_id: `664517`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `109`
- total_duration_ms: `4114191`
- total_duration_us: `4114213255`
- total_tokens_in: `25166786`
- total_tokens_out: `156786`
- total_cost_usd: `71.510182`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 664415 | invoke_cursor_agent#1781161857560#581893231 |  |  | invoke_cursor_agent | finished | 581893 | 581893231 |  |  |  | ["to_prd-axi-cli-detached-dispatcher-20260610-repair2", "prd_grill-axi-cli-detached-dispatcher-20260610-repair2", "to_issues-axi-cli-detached-dispatcher-20260610-repair2", "tdd-axi-cli-detached-dispatcher-20260610-repair2", "tdd_grill-axi-cli-detached-dispatcher-20260610-repair2", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 13, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "axi-cli-detached-dispatcher-20260610", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 664417 | invoke_cursor_agent#1781161857560#581893231 |  |  | invoke_cursor_agent | finished | 581893 | 581893231 |  |  |  | ["to_prd-axi-cli-detached-dispatcher-20260610-repair2", "prd_grill-axi-cli-detached-dispatcher-20260610-repair2", "to_issues-axi-cli-detached-dispatcher-20260610-repair2", "tdd-axi-cli-detached-dispatcher-20260610-repair2", "tdd_grill-axi-cli-detached-dispatcher-20260610-repair2", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 13, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "axi-cli-detached-dispatcher-20260610", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 664418 | invoke_cursor_agent#1781161857560#581893231 |  |  | invoke_cursor_agent | finished | 581893 | 581893231 |  |  |  | ["to_prd-axi-cli-detached-dispatcher-20260610-repair2", "prd_grill-axi-cli-detached-dispatcher-20260610-repair2", "to_issues-axi-cli-detached-dispatcher-20260610-repair2", "tdd-axi-cli-detached-dispatcher-20260610-repair2", "tdd_grill-axi-cli-detached-dispatcher-20260610-repair2", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 13, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "axi-cli-detached-dispatcher-20260610", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 664088 | start_dual_agent_gate#1781161321986#341292847 |  |  | start_dual_agent_gate | completed | 341292 | 341292847 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "execution", "min_subagents": 3, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "axi-cli-detached-dispatcher-20260610", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 664087 | invoke_claude_lead#1781161321995#341272946 |  |  | invoke_claude_lead | completed | 341272 | 341272946 | 3976992 | 7960 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "execution", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "axi-cli-detached-dispatcher-20260610", "timeout_s": 900} | {"cost_usd": 3.1451255000000002, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 8650, "tokens_in": 3976992, "tokens_out": 7960} |  |

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
