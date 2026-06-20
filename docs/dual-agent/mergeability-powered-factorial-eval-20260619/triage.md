# Triage: mergeability-powered-factorial-eval-20260619

- run_id: `2a00f17e-b6b5-4c1e-aa41-e732b5bde482`
- task_id: `mergeability-powered-factorial-eval-20260619`
- final_event_id: `826086`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `183`
- total_duration_ms: `5492623`
- total_duration_us: `5492657946`
- total_tokens_in: `34404704`
- total_tokens_out: `251836`
- total_cost_usd: `114.73615`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 826219 | invoke_cursor_agent#1781930693565#368386915 |  |  | invoke_cursor_agent | finished | 368386 | 368386915 |  |  |  | ["skill-to-prd-mergeability-powered-factorial-eval-20260619", "skill-prd-grill-mergeability-powered-factorial-eval-20260619", "skill-to-issues-mergeability-powered-factorial-eval-20260619", "skill-tdd-mergeability-powered-factorial-eval-20260619", "skill-tdd-grill-mergeability-powered-factorial-eval-20260619", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-tdd-coverage-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1", "runtime-tdd-coverage-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 15, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "mergeability-powered-factorial-eval-20260619", "timeout_s": 3600} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 826220 | invoke_cursor_agent#1781930693565#368386915 |  |  | invoke_cursor_agent | finished | 368386 | 368386915 |  |  |  | ["skill-to-prd-mergeability-powered-factorial-eval-20260619", "skill-prd-grill-mergeability-powered-factorial-eval-20260619", "skill-to-issues-mergeability-powered-factorial-eval-20260619", "skill-tdd-mergeability-powered-factorial-eval-20260619", "skill-tdd-grill-mergeability-powered-factorial-eval-20260619", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-tdd-coverage-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1", "runtime-tdd-coverage-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 15, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "mergeability-powered-factorial-eval-20260619", "timeout_s": 3600} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 826221 | invoke_cursor_agent#1781930693565#368386915 |  |  | invoke_cursor_agent | finished | 368386 | 368386915 |  |  |  | ["skill-to-prd-mergeability-powered-factorial-eval-20260619", "skill-prd-grill-mergeability-powered-factorial-eval-20260619", "skill-to-issues-mergeability-powered-factorial-eval-20260619", "skill-tdd-mergeability-powered-factorial-eval-20260619", "skill-tdd-grill-mergeability-powered-factorial-eval-20260619", "runtime-baseline-execution-1", "runtime-git-diff-execution-1", "runtime-deliverables-execution-1", "runtime-tests-execution-1", "runtime-tdd-coverage-execution-1", "runtime-baseline-outcome_review-1", "runtime-git-diff-outcome_review-1", "runtime-deliverables-outcome_review-1", "runtime-tests-outcome_review-1", "runtime-tdd-coverage-outcome_review-1"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 15, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "mergeability-powered-factorial-eval-20260619", "timeout_s": 3600} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 825887 | invoke_cursor_agent#1781929260239#312985615 |  |  | invoke_cursor_agent | finished | 312985 | 312985615 |  |  |  | ["skill-to-prd-mergeability-powered-factorial-eval-20260619", "skill-prd-grill-mergeability-powered-factorial-eval-20260619", "skill-to-issues-mergeability-powered-factorial-eval-20260619", "skill-tdd-mergeability-powered-factorial-eval-20260619", "skill-tdd-grill-mergeability-powered-factorial-eval-20260619"] | {"gate": "tdd_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 5, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "mergeability-powered-factorial-eval-20260619", "timeout_s": 3600} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 825888 | invoke_cursor_agent#1781929260239#312985615 |  |  | invoke_cursor_agent | finished | 312985 | 312985615 |  |  |  | ["skill-to-prd-mergeability-powered-factorial-eval-20260619", "skill-prd-grill-mergeability-powered-factorial-eval-20260619", "skill-to-issues-mergeability-powered-factorial-eval-20260619", "skill-tdd-mergeability-powered-factorial-eval-20260619", "skill-tdd-grill-mergeability-powered-factorial-eval-20260619"] | {"gate": "tdd_review", "model": null, "planning_artifact_count": 6, "quality": "best", "receipt_count": 5, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "default", "reviewer_output_mode": "cursor_sdk", "task_id": "mergeability-powered-factorial-eval-20260619", "timeout_s": 3600} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |

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
