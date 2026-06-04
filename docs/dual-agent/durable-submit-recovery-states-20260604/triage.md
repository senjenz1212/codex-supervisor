# Triage: durable-submit-recovery-states-20260604

- run_id: `06bedf38-949e-438e-920c-a792426324e2`
- task_id: `durable-submit-recovery-states-20260604`
- final_event_id: `486885`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `108`
- total_duration_ms: `3452912`
- total_duration_us: `3452934186`
- total_tokens_in: `13113892`
- total_tokens_out: `123068`
- total_cost_usd: `64.264353`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 486649 | start_dual_agent_gate#1780563872132#526233798 |  |  | start_dual_agent_gate | completed | 526233 | 526233798 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "implementation_plan", "min_subagents": 3, "planning_artifact_count": 12, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "durable-submit-recovery-states-20260604", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P4": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 486648 | invoke_claude_lead#1780563872141#526213480 |  |  | invoke_claude_lead | completed | 526213 | 526213480 | 926380 | 6881 | P3 |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "implementation_plan", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "durable-submit-recovery-states-20260604", "timeout_s": 900} | {"cost_usd": 7.3023015000000004, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 9399, "tokens_in": 926380, "tokens_out": 6881} |  |
| 487066 | invoke_cursor_agent#1780564993094#395897394 |  |  | invoke_cursor_agent | finished | 395897 | 395897394 |  |  |  | ["skill-to-prd-durable-submit-recovery-states-20260604", "skill-prd-grill-durable-submit-recovery-states-20260604", "skill-to-issues-durable-submit-recovery-states-20260604", "skill-tdd-durable-submit-recovery-states-20260604", "skill-tdd-grill-durable-submit-recovery-states-20260604", "pytest-focused-post-popen-recovery", "pytest-workflow-driver-schema-119-after-post-popen-recovery", "pytest-full-706-after-post-popen-recovery", "git-diff-check-after-post-popen-recovery"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 12, "quality": "best", "receipt_count": 9, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "durable-submit-recovery-states-20260604", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 487067 | invoke_cursor_agent#1780564993094#395897394 |  |  | invoke_cursor_agent | finished | 395897 | 395897394 |  |  |  | ["skill-to-prd-durable-submit-recovery-states-20260604", "skill-prd-grill-durable-submit-recovery-states-20260604", "skill-to-issues-durable-submit-recovery-states-20260604", "skill-tdd-durable-submit-recovery-states-20260604", "skill-tdd-grill-durable-submit-recovery-states-20260604", "pytest-focused-post-popen-recovery", "pytest-workflow-driver-schema-119-after-post-popen-recovery", "pytest-full-706-after-post-popen-recovery", "git-diff-check-after-post-popen-recovery"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 12, "quality": "best", "receipt_count": 9, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "durable-submit-recovery-states-20260604", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |
| 487068 | invoke_cursor_agent#1780564993094#395897394 |  |  | invoke_cursor_agent | finished | 395897 | 395897394 |  |  |  | ["skill-to-prd-durable-submit-recovery-states-20260604", "skill-prd-grill-durable-submit-recovery-states-20260604", "skill-to-issues-durable-submit-recovery-states-20260604", "skill-tdd-durable-submit-recovery-states-20260604", "skill-tdd-grill-durable-submit-recovery-states-20260604", "pytest-focused-post-popen-recovery", "pytest-workflow-driver-schema-119-after-post-popen-recovery", "pytest-full-706-after-post-popen-recovery", "git-diff-check-after-post-popen-recovery"] | {"gate": "outcome_review", "model": null, "planning_artifact_count": 12, "quality": "best", "receipt_count": 9, "reviewer_count": 2, "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"], "reviewer_infra_retry_backoff_s": 1.0, "reviewer_infra_retry_limit": 2, "reviewer_max_tokens": 4096, "reviewer_model": "composer-2.5", "reviewer_output_mode": "cursor_sdk", "task_id": "durable-submit-recovery-states-20260604", "timeout_s": 900} | {"accepted": true, "failure_classification": null, "fallback_from_runtime": null, "fallback_reason": null, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green", "recoverable": false, "reviewer_assurance": "tool_backed_primary", "reviewer_output_mode": "cursor_sdk", "reviewer_runtime": "cursor_sdk"} |  |

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
