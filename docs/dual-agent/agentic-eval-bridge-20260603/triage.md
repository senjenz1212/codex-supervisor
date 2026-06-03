# Triage: agentic-eval-bridge-20260603

- run_id: `codex-agentic-eval-bridge-20260603-9280a512-1274-45a7-9335-f5325e2665f4`
- task_id: `agentic-eval-bridge-20260603`
- final_event_id: `463903`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `113`
- total_duration_ms: `2975406`
- total_duration_us: `2975431995`
- total_tokens_in: `15804122`
- total_tokens_out: `132266`
- total_cost_usd: `60.491084`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 464261 | invoke_cursor_agent#1780505751663#385824569 |  |  | invoke_cursor_agent | finished | 385824 | 385824569 |  |  |  | ["skill-to-prd-agentic-eval-bridge-20260603", "skill-prd-grill-agentic-eval-bridge-20260603", "skill-to-issues-agentic-eval-bridge-20260603", "skill-tdd-agentic-eval-bridge-20260603", "skill-tdd-grill-agentic-eval-bridge-20260603", "pytest-focused-agentic-eval-bridge", "pytest-related-agentic-eval-bridge", "py-compile-agentic-eval-bridge", "pytest-full-agentic-eval-bridge", "git-diff-check-agentic-eval-bridge", "live-three-arm-agentic-eval-bridge"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 11, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-bridge-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 464262 | invoke_cursor_agent#1780505751663#385824569 |  |  | invoke_cursor_agent | finished | 385824 | 385824569 |  |  |  | ["skill-to-prd-agentic-eval-bridge-20260603", "skill-prd-grill-agentic-eval-bridge-20260603", "skill-to-issues-agentic-eval-bridge-20260603", "skill-tdd-agentic-eval-bridge-20260603", "skill-tdd-grill-agentic-eval-bridge-20260603", "pytest-focused-agentic-eval-bridge", "pytest-related-agentic-eval-bridge", "py-compile-agentic-eval-bridge", "pytest-full-agentic-eval-bridge", "git-diff-check-agentic-eval-bridge", "live-three-arm-agentic-eval-bridge"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 11, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-bridge-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 464263 | invoke_cursor_agent#1780505751663#385824569 |  |  | invoke_cursor_agent | finished | 385824 | 385824569 |  |  |  | ["skill-to-prd-agentic-eval-bridge-20260603", "skill-prd-grill-agentic-eval-bridge-20260603", "skill-to-issues-agentic-eval-bridge-20260603", "skill-tdd-agentic-eval-bridge-20260603", "skill-tdd-grill-agentic-eval-bridge-20260603", "pytest-focused-agentic-eval-bridge", "pytest-related-agentic-eval-bridge", "py-compile-agentic-eval-bridge", "pytest-full-agentic-eval-bridge", "git-diff-check-agentic-eval-bridge", "live-three-arm-agentic-eval-bridge"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 11, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-bridge-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 463581 | invoke_cursor_agent#1780505016016#334639505 |  |  | invoke_cursor_agent | finished | 334639 | 334639505 |  |  |  | ["skill-to-prd-agentic-eval-bridge-20260603", "skill-prd-grill-agentic-eval-bridge-20260603", "skill-to-issues-agentic-eval-bridge-20260603", "skill-tdd-agentic-eval-bridge-20260603", "skill-tdd-grill-agentic-eval-bridge-20260603", "pytest-focused-agentic-eval-bridge", "pytest-related-agentic-eval-bridge", "py-compile-agentic-eval-bridge", "pytest-full-agentic-eval-bridge", "git-diff-check-agentic-eval-bridge", "live-three-arm-agentic-eval-bridge"] | {"gate": "implementation_plan", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 11, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-bridge-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 463582 | invoke_cursor_agent#1780505016016#334639505 |  |  | invoke_cursor_agent | finished | 334639 | 334639505 |  |  |  | ["skill-to-prd-agentic-eval-bridge-20260603", "skill-prd-grill-agentic-eval-bridge-20260603", "skill-to-issues-agentic-eval-bridge-20260603", "skill-tdd-agentic-eval-bridge-20260603", "skill-tdd-grill-agentic-eval-bridge-20260603", "pytest-focused-agentic-eval-bridge", "pytest-related-agentic-eval-bridge", "py-compile-agentic-eval-bridge", "pytest-full-agentic-eval-bridge", "git-diff-check-agentic-eval-bridge", "live-three-arm-agentic-eval-bridge"] | {"gate": "implementation_plan", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 11, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-bridge-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |

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
