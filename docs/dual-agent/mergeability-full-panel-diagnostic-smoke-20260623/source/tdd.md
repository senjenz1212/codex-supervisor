# TDD Plan

## Public Boundary First

### test_configured_full_panel_smoke_writes_paired_acceptance_report

Maps to: Slice 1, P1, P3, P5.

RED: Invoke the configured full-panel mergeability smoke and assert paired_acceptance_report.json is written with reviewer_panel_mode configured.

GREEN: Wire the smoke path only enough to persist the report or to expose the explicit unavailable reason.

### test_configured_full_panel_smoke_records_cursor_isolation

Maps to: Slice 1, P2.

RED: Assert the report contains Cursor SDK worktree isolation diagnostics and does not pass with source cursor_modified_worktree.

GREEN: Preserve or expose the existing Cursor isolation diagnostic fields in the report evidence.

### test_configured_full_panel_requires_cursor_and_codex_verdicts

Maps to: Slice 1, P3.

RED: Assert S_full availability requires both Cursor and Codex verdict evidence when the full roster is expected.

GREEN: Keep missing verdicts unavailable and separate infrastructure failure from quality rejection.

### test_configured_full_panel_marginal_has_status_or_reason

Maps to: Slice 1, P4.

RED: Assert panel_marginal_delta_at_matched_true_accept returns computed, not_matched, unavailable, or insufficient_candidate_pool with a concrete reason when not computed.

GREEN: Preserve current panel marginal semantics and report the exact status.

### test_configured_full_panel_blocks_oracle_packet_leak

Maps to: Slice 2, P3.

RED: Assert hidden oracle material in reviewer packets triggers an isolation failure.

GREEN: Reuse the existing public packet leak detector and keep hidden oracle outcomes outside reviewer criteria.

### test_configured_full_panel_report_only_invariants_false

Maps to: Slice 2, P5.

RED: Assert metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced are false.

GREEN: Keep the diagnostic report-only and block policy derivation.

## Verification Commands

- python -m pytest tests/test_mergeability_bench.py::test_configured_full_panel_smoke_writes_paired_acceptance_report
- python -m pytest tests/test_mergeability_bench.py::test_configured_full_panel_smoke_records_cursor_isolation
- python -m pytest tests/test_mergeability_bench.py::test_configured_full_panel_requires_cursor_and_codex_verdicts
- python -m pytest tests/test_mergeability_bench.py::test_configured_full_panel_marginal_has_status_or_reason
- python -m pytest tests/test_mergeability_bench.py::test_configured_full_panel_blocks_oracle_packet_leak
- python -m pytest tests/test_mergeability_bench.py::test_configured_full_panel_report_only_invariants_false
