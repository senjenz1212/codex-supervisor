# Triage: durable-workflow-job-extraction-plan-20260603

- run_id: `3bd54516-a9e3-4eea-9bd2-f58a79a0d693`
- task_id: `durable-workflow-job-extraction-plan-20260603`
- final_event_id: `475158`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `104`
- total_duration_ms: `2618107`
- total_duration_us: `2618131370`
- total_tokens_in: `16471202`
- total_tokens_out: `141580`
- total_cost_usd: `63.1783`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 474914 | invoke_cursor_agent#1780526585725#236817613 |  |  | invoke_cursor_agent | finished | 236817 | 236817613 |  |  |  | ["skill-to-prd-durable-workflow-job-extraction-plan-20260603", "skill-prd-grill-durable-workflow-job-extraction-plan-20260603", "skill-to-issues-durable-workflow-job-extraction-plan-20260603", "skill-tdd-durable-workflow-job-extraction-plan-20260603", "skill-tdd-grill-durable-workflow-job-extraction-plan-20260603", "agentic-worker-boundary-review", "agentic-worker-dependency-graph", "agentic-worker-surface-map", "agentic-worker-test-inventory"] | {"gate": "tdd_review", "model": null, "planning_artifact_count": 7, "quality": "best", "receipt_count": 9, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "durable-workflow-job-extraction-plan-20260603", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 474915 | invoke_cursor_agent#1780526585725#236817613 |  |  | invoke_cursor_agent | finished | 236817 | 236817613 |  |  |  | ["skill-to-prd-durable-workflow-job-extraction-plan-20260603", "skill-prd-grill-durable-workflow-job-extraction-plan-20260603", "skill-to-issues-durable-workflow-job-extraction-plan-20260603", "skill-tdd-durable-workflow-job-extraction-plan-20260603", "skill-tdd-grill-durable-workflow-job-extraction-plan-20260603", "agentic-worker-boundary-review", "agentic-worker-dependency-graph", "agentic-worker-surface-map", "agentic-worker-test-inventory"] | {"gate": "tdd_review", "model": null, "planning_artifact_count": 7, "quality": "best", "receipt_count": 9, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "durable-workflow-job-extraction-plan-20260603", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 474916 | invoke_cursor_agent#1780526585725#236817613 |  |  | invoke_cursor_agent | finished | 236817 | 236817613 |  |  |  | ["skill-to-prd-durable-workflow-job-extraction-plan-20260603", "skill-prd-grill-durable-workflow-job-extraction-plan-20260603", "skill-to-issues-durable-workflow-job-extraction-plan-20260603", "skill-tdd-durable-workflow-job-extraction-plan-20260603", "skill-tdd-grill-durable-workflow-job-extraction-plan-20260603", "agentic-worker-boundary-review", "agentic-worker-dependency-graph", "agentic-worker-surface-map", "agentic-worker-test-inventory"] | {"gate": "tdd_review", "model": null, "planning_artifact_count": 7, "quality": "best", "receipt_count": 9, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "durable-workflow-job-extraction-plan-20260603", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 475310 | invoke_cursor_agent#1780527479290#196883515 |  |  | invoke_cursor_agent | finished | 196883 | 196883515 |  |  |  | ["skill-to-prd-durable-workflow-job-extraction-plan-20260603", "skill-prd-grill-durable-workflow-job-extraction-plan-20260603", "skill-to-issues-durable-workflow-job-extraction-plan-20260603", "skill-tdd-durable-workflow-job-extraction-plan-20260603", "skill-tdd-grill-durable-workflow-job-extraction-plan-20260603", "agentic-worker-boundary-review", "agentic-worker-dependency-graph", "agentic-worker-surface-map", "agentic-worker-test-inventory"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 7, "quality": "best", "receipt_count": 9, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "durable-workflow-job-extraction-plan-20260603", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 475311 | invoke_cursor_agent#1780527479290#196883515 |  |  | invoke_cursor_agent | finished | 196883 | 196883515 |  |  |  | ["skill-to-prd-durable-workflow-job-extraction-plan-20260603", "skill-prd-grill-durable-workflow-job-extraction-plan-20260603", "skill-to-issues-durable-workflow-job-extraction-plan-20260603", "skill-tdd-durable-workflow-job-extraction-plan-20260603", "skill-tdd-grill-durable-workflow-job-extraction-plan-20260603", "agentic-worker-boundary-review", "agentic-worker-dependency-graph", "agentic-worker-surface-map", "agentic-worker-test-inventory"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 7, "quality": "best", "receipt_count": 9, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "durable-workflow-job-extraction-plan-20260603", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |

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
