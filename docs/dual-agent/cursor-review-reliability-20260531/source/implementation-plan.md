# Cursor Review Reliability Implementation Plan

## Files / Modules To Touch

- `supervisor/cursor_agent.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `supervisor/agent_mailbox.py`
- `tests/test_cursor_agent.py`
- `tests/test_dual_agent_workflow_driver.py`
- `docs/dual-agent/cursor-review-reliability-20260531/source/tdd.md`

## Steps

1. Add focused tests for Cursor contract retry and terminal infrastructure classification.
2. Add bounded retry support to `invoke_cursor_agent`, keeping valid `Outcome` handling unchanged.
3. Add a typed infrastructure classification for repeated malformed output without fabricating a reviewer outcome.
4. Update workflow decision payloads so Cursor infrastructure failure is recorded separately from valid Cursor revise/deny.
5. Persist classification in existing interaction and `tri_agent_cursor_review` events so transcript reads consume durable state.
6. Run focused tests, then the full dev suite if dependency health allows.

## Risks

- Treating infrastructure failure as acceptance would weaken independent review, so tests must assert it is never counted as accept.
- Retrying at the wrong layer could duplicate Cursor calls without ledger evidence, so retry count and terminal classification must be visible.
- A valid Cursor revise regression would be subtle because both infrastructure and quality failures are non-accepting; regression tests must distinguish reasons.
- Cursor SDK exception classes can drift, so tests should use injected fake runners rather than live SDK calls.

## Traceability

- P1 -> `test_invoke_cursor_agent_retries_missing_outcome_with_contract_packet`
- P2 -> `test_cursor_contract_miss_returns_reviewer_infrastructure_unavailable`
- P3 -> `test_valid_cursor_revise_still_blocks_after_retry_hardening`
- P4 -> `test_workflow_records_cursor_infrastructure_failure_without_counting_accept`
- P5 -> `test_read_gate_transcript_preserves_persisted_cursor_infrastructure_verdict`
