# Reviewer Unavailable Recovery TDD Plan

## Public Boundary

Use `mcp_tools.codex_supervisor_stdio.run_dual_agent_workflow`,
`read_gate_transcript`, and
`mcp_tools.codex_supervisor_workflow_cli.workflow_kwargs_from_payload`. The
first RED proof hits `run_dual_agent_workflow`, because the exact failure is the
workflow branch that turns recoverable Cursor infrastructure failures into a
terminal blocked result.

## Test Cases

### test_workflow_kwargs_from_payload_preserves_reviewer_unavailable_policy

Maps to: ISS-1, P1
RED: Add `reviewer_unavailable_policy` to a workflow CLI payload and assert it
is dropped.
GREEN: Add the key to `WORKFLOW_KEYS` and thread the parameter through the MCP
workflow entrypoint.

### test_reviewer_unavailable_block_policy_preserves_current_block

Maps to: ISS-1, P4
RED: Existing Cursor contract-miss fixture defaults to blocked behavior.
GREEN: Preserve the same hard block when explicit policy is `block`.

### test_reviewer_unavailable_proceed_degraded_advances_with_degraded_receipt

Maps to: ISS-2, P2
RED: With `proceed_degraded`, a recoverable Cursor contract miss still returns
blocked and never reaches the later gate.
GREEN: Advance on Claude plus Codex acceptance, record degraded
reviewer-unavailable evidence, and keep Cursor `accepted=false`.

### test_reviewer_unavailable_default_escalates_and_resume_continue_advances

Maps to: ISS-3, P3
RED: Default policy returns a dead-end block without a resumable action.
GREEN: Record a paused human escalation; a continue resume lets the next run
advance degraded with authorization evidence.

### test_reviewer_unavailable_proceed_degraded_escalates_for_agentic_required

Maps to: ISS-4, P6
RED: Requested `proceed_degraded` auto-advances even when
`agentic_lead_policy=required`.
GREEN: Escalate instead of auto-proceeding.

### test_reviewer_unavailable_runtime_native_escalates

Maps to: ISS-4, P6
RED: Requested `proceed_degraded` auto-advances even when runtime-native
evidence is required.
GREEN: Escalate instead of auto-proceeding.

### test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection

Maps to: ISS-4, P5
RED: A valid Cursor `revise` or `deny` is treated like unavailable
infrastructure.
GREEN: Real reviewer decisions remain on the existing blocking path.

## RED/GREEN Plan

RED: Add CLI preservation and default-policy workflow tests.
GREEN: Add config default, canonicalization, CLI key, MCP parameter threading.

RED: Add proceed-degraded workflow test using PR #2's recoverable Cursor
contract-miss result fixture.
GREEN: Rework only the infrastructure-failure branch to record degraded receipt
evidence and advance on available reviewer acceptance.

RED: Add default escalation/resume test.
GREEN: Create a paused human action using the existing resume signal action type
and consume the continue signal on rerun.

RED: Add high-stakes safety tests.
GREEN: Route agentic-required, runtime-native required, and user-facing evidence
paths to escalation instead of auto-proceed.
