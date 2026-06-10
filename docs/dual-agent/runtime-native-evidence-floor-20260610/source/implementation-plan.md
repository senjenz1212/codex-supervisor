# Runtime-Native Evidence Floor Implementation Plan

## Files / Modules To Touch

- `supervisor/runtime_evidence.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `supervisor/dual_agent_workflow.py`
- `supervisor/state.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_codex_supervisor_mcp_stdio.py`

## Risks

- Runtime validation can be too strict for tests that previously used synthetic temp directories with no git repository. The test fixtures must model a real baseline and post-baseline file changes instead of weakening production behavior.
- Rerunning tests in a copied validation worktree can be expensive or accidentally use the wrong interpreter. The implementation should reuse the active interpreter for path-style test declarations and cap per-command timeout.
- Gate ordering can accidentally let Cursor or Codex accept before runtime evidence has been appended to the receipt list. Runtime evidence must be collected before claim verification, deliverable verification, Cursor review, and final Codex decision.
- Generated workflow artifacts may appear as extra worktree changes. Extra files should be recorded for review without causing failure unless declared deliverables are missing.

## Traceability

- P1 -> test_execution_gate_accepts_supervisor_runtime_native_receipts
- P2 -> test_execution_gate_rejects_fabricated_runtime_receipts_for_missing_file
- P3 -> test_execution_gate_rejects_fabricated_runtime_receipts_for_missing_file
- P4 -> test_outcome_review_requires_supervisor_rerun_for_tests_passed_claim
- P5 -> test_agent_supplied_tests_passed_receipt_without_supervisor_rerun_fails
- P6 -> test_read_gate_transcript_includes_runtime_evidence_events
