# Dual-Agent Branch A+B Hardening Implementation Plan

## Files / Modules To Touch

- `supervisor/failure_taxonomy.py`
- `supervisor/trace_envelope.py`
- `supervisor/state.py`
- `supervisor/agent_mailbox.py`
- `supervisor/dual_agent_workflow.py`
- `supervisor/agent_interaction_snapshot.py`
- `supervisor/dual_agent_artifacts.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `scripts/probe_cursor_sdk_live.py`
- `skills/dual-agent-gate.md`
- `docs/testing/dual-agent-harness-health-matrix.md`
- `docs/testing/dual-agent-slice0-coverage-index.md`
- `tests/test_failure_taxonomy.py`
- `tests/test_agent_mailbox.py`
- `tests/test_agent_interaction_snapshot.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_codex_supervisor_mcp_stdio.py`

## Steps

1. Add deterministic failure taxonomy helpers and central blocking probe ids.
2. Stamp dual-agent events with a trace envelope inside `State.write_event`.
3. Add PRD/TDD skill receipt verification and block workflow start on missing receipts.
4. Add skill receipt validation to transcript reads and artifact export.
5. Add Codex review packet support and use `ConfidenceReport.value` for gate-round confidence.
6. Add a live Cursor SDK probe script that records skipped/blocked/completed fixtures.
7. Update health/coverage docs and the dual-agent skill instructions.
8. Run focused tests, full pytest, compileall, and secret scan before commit.

## Risks

- Adding event metadata could break consumers expecting exact payload equality.
- Requiring skill receipts by default may force callers to update workflow invocations.
- Cursor SDK live probing cannot be completed without a key in the environment.
- Trace envelopes must remain non-breaking: no wrapper migration, no table migration.

## Traceability

- P1 -> test_failure_taxonomy_classifies_receipt_and_skill_gaps
- P2 -> test_trace_envelope_stamps_dual_agent_payloads_without_wrapping_original_shape
- P3 -> test_run_dual_agent_workflow_requires_prd_tdd_skill_receipts
- P4 -> test_codex_confidence_report_and_review_packet_explain_decision
- P5 -> scripts/probe_cursor_sdk_live.py diagnostic fixture path
