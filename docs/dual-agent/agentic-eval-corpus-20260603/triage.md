# Triage: agentic-eval-corpus-20260603

- run_id: `codex-agentic-eval-corpus-20260603-a12e759f-6920-4b52-9ed9-32322c4a401f`
- task_id: `agentic-eval-corpus-20260603`
- final_event_id: `454634`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `109`
- total_duration_ms: `2952873`
- total_duration_us: `2952896604`
- total_tokens_in: `17016152`
- total_tokens_out: `127592`
- total_cost_usd: `62.067687`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 453844 | invoke_cursor_agent#1780469704763#367305392 |  |  | invoke_cursor_agent | finished | 367305 | 367305392 |  |  |  | ["skill-to-prd-agentic-eval-corpus-20260603", "skill-prd-grill-agentic-eval-corpus-20260603", "skill-to-issues-agentic-eval-corpus-20260603", "skill-tdd-agentic-eval-corpus-20260603", "skill-tdd-grill-agentic-eval-corpus-20260603", "planning-validator-agentic-eval-corpus-20260603", "pytest-focused-agentic-eval-corpus-20260603", "py-compile-agentic-eval-corpus-20260603", "pytest-relevant-agentic-eval-corpus-20260603", "git-diff-check-agentic-eval-corpus-20260603", "pytest-full-agentic-eval-corpus-20260603", "seed-corpus-agentic-eval-corpus-20260603", "handoff-miner-staging-agentic-eval-corpus-20260603", "test-evidence-agentic-eval-corpus-20260603"] | {"gate": "tdd_review", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 14, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-corpus-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 453845 | invoke_cursor_agent#1780469704763#367305392 |  |  | invoke_cursor_agent | finished | 367305 | 367305392 |  |  |  | ["skill-to-prd-agentic-eval-corpus-20260603", "skill-prd-grill-agentic-eval-corpus-20260603", "skill-to-issues-agentic-eval-corpus-20260603", "skill-tdd-agentic-eval-corpus-20260603", "skill-tdd-grill-agentic-eval-corpus-20260603", "planning-validator-agentic-eval-corpus-20260603", "pytest-focused-agentic-eval-corpus-20260603", "py-compile-agentic-eval-corpus-20260603", "pytest-relevant-agentic-eval-corpus-20260603", "git-diff-check-agentic-eval-corpus-20260603", "pytest-full-agentic-eval-corpus-20260603", "seed-corpus-agentic-eval-corpus-20260603", "handoff-miner-staging-agentic-eval-corpus-20260603", "test-evidence-agentic-eval-corpus-20260603"] | {"gate": "tdd_review", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 14, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-corpus-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 453846 | invoke_cursor_agent#1780469704763#367305392 |  |  | invoke_cursor_agent | finished | 367305 | 367305392 |  |  |  | ["skill-to-prd-agentic-eval-corpus-20260603", "skill-prd-grill-agentic-eval-corpus-20260603", "skill-to-issues-agentic-eval-corpus-20260603", "skill-tdd-agentic-eval-corpus-20260603", "skill-tdd-grill-agentic-eval-corpus-20260603", "planning-validator-agentic-eval-corpus-20260603", "pytest-focused-agentic-eval-corpus-20260603", "py-compile-agentic-eval-corpus-20260603", "pytest-relevant-agentic-eval-corpus-20260603", "git-diff-check-agentic-eval-corpus-20260603", "pytest-full-agentic-eval-corpus-20260603", "seed-corpus-agentic-eval-corpus-20260603", "handoff-miner-staging-agentic-eval-corpus-20260603", "test-evidence-agentic-eval-corpus-20260603"] | {"gate": "tdd_review", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 14, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-corpus-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 454096 | invoke_cursor_agent#1780470195048#272603193 |  |  | invoke_cursor_agent | finished | 272603 | 272603193 |  |  |  | ["skill-to-prd-agentic-eval-corpus-20260603", "skill-prd-grill-agentic-eval-corpus-20260603", "skill-to-issues-agentic-eval-corpus-20260603", "skill-tdd-agentic-eval-corpus-20260603", "skill-tdd-grill-agentic-eval-corpus-20260603", "planning-validator-agentic-eval-corpus-20260603", "pytest-focused-agentic-eval-corpus-20260603", "py-compile-agentic-eval-corpus-20260603", "pytest-relevant-agentic-eval-corpus-20260603", "git-diff-check-agentic-eval-corpus-20260603", "pytest-full-agentic-eval-corpus-20260603", "seed-corpus-agentic-eval-corpus-20260603", "handoff-miner-staging-agentic-eval-corpus-20260603", "test-evidence-agentic-eval-corpus-20260603"] | {"gate": "implementation_plan", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 14, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-corpus-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": "cursor_sdk", "fallback_reason": "reviewer_invocation_failed", "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "fallback_text_only", "reviewer_output_mode": "litellm_structured", "reviewer_runtime": "litellm_structured"} |  |
| 454098 | invoke_cursor_agent#1780470195048#272603193 |  |  | invoke_cursor_agent | finished | 272603 | 272603193 |  |  |  | ["skill-to-prd-agentic-eval-corpus-20260603", "skill-prd-grill-agentic-eval-corpus-20260603", "skill-to-issues-agentic-eval-corpus-20260603", "skill-tdd-agentic-eval-corpus-20260603", "skill-tdd-grill-agentic-eval-corpus-20260603", "planning-validator-agentic-eval-corpus-20260603", "pytest-focused-agentic-eval-corpus-20260603", "py-compile-agentic-eval-corpus-20260603", "pytest-relevant-agentic-eval-corpus-20260603", "git-diff-check-agentic-eval-corpus-20260603", "pytest-full-agentic-eval-corpus-20260603", "seed-corpus-agentic-eval-corpus-20260603", "handoff-miner-staging-agentic-eval-corpus-20260603", "test-evidence-agentic-eval-corpus-20260603"] | {"gate": "implementation_plan", "model": null, "planning_artifact_count": 13, "quality": "best", "receipt_count": 14, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "agentic-eval-corpus-20260603", "timeout_s": 1200} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": "cursor_sdk", "fallback_reason": "reviewer_invocation_failed", "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "fallback_text_only", "reviewer_output_mode": "litellm_structured", "reviewer_runtime": "litellm_structured"} |  |

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
