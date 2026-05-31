# Reviewer Unavailable Recovery Implementation Plan

## Files / Modules To Touch

- `supervisor/config.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_workflow_cli.py`
- `supervisor/agent_mailbox.py`
- `supervisor/state.py`
- `supervisor/dual_agent_artifacts.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_codex_supervisor_mcp_stdio.py`

## Risks

- Degraded recovery could be mistaken for Cursor acceptance if the receipt is
  not explicit.
- Default escalation could remain a dead end if it does not create a resumable
  action.
- Review packet force-next-round behavior could continue to block degraded
  recovery unless it recognizes policy-authorized degraded evidence.
- High-stakes evidence paths could auto-proceed if the safety predicate is too
  narrow.

## Traceability

- P1 -> `test_workflow_kwargs_from_payload_preserves_reviewer_unavailable_policy`
- P2 -> `test_reviewer_unavailable_proceed_degraded_advances_with_degraded_receipt`
- P3 -> `test_reviewer_unavailable_default_escalates_and_resume_continue_advances`
- P4 -> `test_reviewer_unavailable_block_policy_preserves_current_block`
- P5 -> `test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection`
- P6 -> `test_reviewer_unavailable_proceed_degraded_escalates_for_agentic_required`
- P6 -> `test_reviewer_unavailable_runtime_native_escalates`

## Steps

1. Add the policy surface and CLI payload threading.
2. Add RED workflow tests for block, default escalate, proceed degraded, and
   high-stakes escalation.
3. Implement the reviewer-unavailable recovery helper and ledger receipt.
4. Extend transcript/artifact read-side allowlists for the recovery receipt.
5. Run focused tests, then the full suite.
