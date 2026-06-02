# TDD Plan: Reviewer Route Access Fix

## test_structured_litellm_access_denied_classifies_distinctly_without_retry

Maps to: P2, Slice 1

RED: A simulated LiteLLM/Gemini 403 bubbles through generic exception handling,
is classified as `reviewer_infrastructure_unavailable`, and can be absorbed by
reviewer-unavailable recovery.

GREEN: The same 403 returns a red `reviewer_access_denied` probe, classification
`reviewer_access_denied`, `recoverable=false`, one attempt, sanitized
diagnostics, and no retry.

## test_cursor_sdk_access_denied_does_not_retry_or_fallback

Maps to: P2, P4, Slice 1

RED: A Cursor SDK permission error is retried as generic infrastructure and may
fall back in a way that hides a primary-route access/configuration failure.

GREEN: The error returns immediately as `reviewer_access_denied`, records the
primary runtime context, and does not enter fallback.

## test_structured_fallback_prompt_compacts_large_receipts

Maps to: P1, P4, Slice 2

RED: A structured fallback prompt includes huge receipt `result` payloads, so
large rigorous gates can hit `finish_reason=length` before emitting a useful
typed outcome.

GREEN: The structured fallback prompt keeps receipt identity/status/claims and
truncates large values while retaining the typed outcome contract and critical
review instructions.

## test_cursor_sdk_failure_falls_back_to_litellm_structured_with_lower_assurance

Maps to: P1, P4, Slice 2

RED: Cursor SDK infrastructure failure leaves the workflow with no counted
reviewer when Gemini fallback is configured.

GREEN: Existing fallback path returns a valid typed accept from
`litellm_structured`, records `fallback_from_runtime=cursor_sdk`, and reports
lower text-only assurance.

## test_reviewer_access_denied_blocks_without_degraded_recovery

Maps to: P2, P3, Slice 3

RED: Workflow logic treats access-denied like reviewer infrastructure
unavailable and can write degraded recovery evidence.

GREEN: The workflow blocks with access-denied classification in the cursor
review payload, records no reviewer-unavailable recovery event, and names access
denied in the gate objection.

## test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection

Maps to: P5, Slice 4

RED: A valid reviewer `revise` or `deny` could be conflated with unavailable
infrastructure.

GREEN: Existing revise/deny tests remain blocking and do not enter access or
degraded recovery paths.

## Regression Commands

- `uv run --extra dev pytest tests/test_cursor_agent.py tests/test_dual_agent_workflow_driver.py -q`
- `uv run --extra dev pytest -q`
