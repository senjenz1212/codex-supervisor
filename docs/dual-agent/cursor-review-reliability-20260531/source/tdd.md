# Cursor Review Reliability TDD Plan

## Public Boundary

Use `supervisor.cursor_agent.invoke_cursor_agent`, `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`, and `read_gate_transcript`. The first proof must hit `invoke_cursor_agent`, because the failure begins when Cursor returns a transcript without a parseable typed outcome.

## Test Cases

### test_invoke_cursor_agent_retries_missing_outcome_with_contract_packet

Maps to: ISS-1, P1
RED: Fake the Cursor SDK to return text without `<dual_agent_outcome>` and assert no corrective retry occurs.
GREEN: Add bounded retry support; assert the second prompt includes the exact outcome contract and that a later valid outcome returns `cursor_review_ok`.

### test_cursor_contract_miss_returns_reviewer_infrastructure_unavailable

Maps to: ISS-2, P2, P4
RED: Fake every Cursor response as malformed and observe a bare missing-outcome red probe.
GREEN: Return a deterministic infrastructure classification such as `reviewer_contract_unmet`, with retry count and transcript tail, and with `outcome is None`.

### test_workflow_records_cursor_infrastructure_failure_without_counting_accept

Maps to: ISS-3, P4
RED: Run a workflow gate with Cursor enabled and malformed Cursor output; assert the current gate blocks with `cursor_review_failed` and cannot distinguish infrastructure from quality.
GREEN: Record infrastructure classification in the gate payload and round objection; assert the missing verdict is not counted as accept and recovery/escalation is deterministic.

### test_valid_cursor_revise_still_blocks_after_retry_hardening

Maps to: ISS-3, ISS-5, P3
RED: Add a valid Cursor `revise` fixture and protect against any implementation that treats infrastructure recovery as acceptance.
GREEN: Preserve existing AND-verdict behavior: valid Cursor revise blocks, valid accept can advance, and `cursor_modified_worktree` remains red.

### test_read_gate_transcript_preserves_persisted_cursor_infrastructure_verdict

Maps to: ISS-4, P5
RED: Persist a Cursor infrastructure event and simulate a later transcript read/export transport failure path; assert the durable event is still missing or not classified.
GREEN: Store the typed classification in ledger events and ensure `read_gate_transcript` returns it from persisted state without re-calling Cursor.

## RED/GREEN Plan

RED: Add the Cursor invocation retry test using a fake SDK runner or injected runner hook.
GREEN: Add retry loop and corrective prompt generation inside `invoke_cursor_agent`.

RED: Add the repeated malformed transcript test and assert the current red probe is untyped infrastructure.
GREEN: Add a typed infrastructure classification result with no fabricated `Outcome`.

RED: Add workflow-level coverage showing malformed Cursor output creates limbo.
GREEN: Teach the gate policy to distinguish infrastructure from valid reviewer decisions and record the recovery mode.

RED: Add valid Cursor revise and valid accept regressions.
GREEN: Keep `cursor_accepts` and Codex decision logic unchanged for valid outcomes.

RED: Add durable transcript/ledger evidence coverage.
GREEN: Persist the classification in `tri_agent_cursor_review` and interaction metadata before any artifact export or transcript read.
