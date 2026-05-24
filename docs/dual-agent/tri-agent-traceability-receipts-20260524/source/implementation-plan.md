# Implementation Plan

## Files / Modules To Touch

- `supervisor/agent_mailbox.py`
- `supervisor/dual_agent_workflow.py`
- `supervisor/dual_agent_artifacts.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `skills/dual-agent-gate.md`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_dual_agent_artifacts.py`
- `tests/test_cursor_agent.py`
- `docs/testing/dual-agent-slice0-coverage-index.md`
- `docs/testing/dual-agent-harness-health-matrix.md`

## Steps

1. Add RED tests for rich mailbox payloads, receipt-backed claims, resume context, and Markdown export.
2. Extend `AgentMailboxMessage` with trace fields while preserving backward-compatible defaults.
3. Add workflow receipt normalization and claim-verification checks.
4. Thread `tool_receipts` through `run_dual_agent_workflow` and Cursor review prompts.
5. Expand resume prompt output and transcript/export rendering.
6. Update docs and trace artifacts.
7. Run focused suite, full suite, and compileall.

## Risks

- Existing happy-path workflow tests may fail once "tests passed" requires receipts. Mitigation: make fixture receipts explicit in tests so the stronger contract is visible.
- Receipt dictionaries may become inconsistent. Mitigation: normalize receipt kind/status in one helper and document accepted kinds.
- Overly strict receipt checks could block artifact-only review. Mitigation: checks trigger only when built-in claims appear or user-facing visual evidence is required.

## Traceability

- P1 -> test_agent_mailbox_message_carries_trace_fields
- P2 -> test_run_dual_agent_workflow_requires_test_and_diff_receipts_for_claims
- P2 -> test_run_dual_agent_workflow_verified_claims_string_does_not_satisfy_push
- P2 -> test_run_dual_agent_workflow_accepts_receipt_backed_claims
- P3 -> test_workflow_resume_prompt_includes_trace_context
- P4 -> test_export_dual_agent_run_artifacts_renders_interaction_receipts
