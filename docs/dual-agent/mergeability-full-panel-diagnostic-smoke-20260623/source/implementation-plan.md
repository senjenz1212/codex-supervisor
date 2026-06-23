# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `supervisor/cursor_agent.py`
- `tests/test_mergeability_bench.py`
- `tests/test_cursor_agent.py`
- `docs/dual-agent/mergeability-full-panel-diagnostic-smoke-20260623/source/prd.md`
- `docs/dual-agent/mergeability-full-panel-diagnostic-smoke-20260623/source/issues.md`
- `docs/dual-agent/mergeability-full-panel-diagnostic-smoke-20260623/source/tdd.md`

## Plan

Use the existing mergeability calibration seam and avoid broad refactors. First verify whether the current run_configured_panel_diagnostic or run_paired_acceptance_pilot path can persist the requested diagnostic report with reviewer_panel_mode configured. If it can, execute it and add only artifact evidence. If it cannot, add the smallest public helper that wraps the same seam and writes paired_acceptance_report.json without changing evaluator policy.

## Risks

- The configured Cursor reviewer can still fail for infrastructure reasons even after isolated worktree hardening, so the report must classify unavailable evidence instead of implying quality rejection.
- The small fixture corpus can produce not_matched panel marginal status when S_full lowers true accept rate, so the diagnostic must not overstate an improvement number.
- Reviewer packet evidence can accidentally drift toward oracle material if hidden fields are copied into reports, so leak detection must stay in the public boundary path.

## Traceability

- P1 maps to test_configured_full_panel_smoke_writes_paired_acceptance_report through Slice 1.
- P2 maps to test_configured_full_panel_smoke_records_cursor_isolation through Slice 1.
- P3 maps to test_configured_full_panel_requires_cursor_and_codex_verdicts and test_configured_full_panel_blocks_oracle_packet_leak through both slices.
- P4 maps to test_configured_full_panel_marginal_has_status_or_reason through Slice 1.
- P5 maps to test_configured_full_panel_report_only_invariants_false through Slice 2.

## Guardrails

Keep hidden oracle fields out of reviewer packets, keep hidden oracle outcomes post-decision, and leave every policy or improvement flag disabled. Treat missing Cursor or Codex reviewer verdicts as unavailable full-panel evidence. Do not add a rubric, do not grow the corpus, and do not use the oracle ceiling as a supervisor result.

## Review Evidence

The outcome review packet must include changed files, declared tests, executed tests, runtime receipts, reviewer context receipts, Cursor worktree isolation diagnostics, reviewer panel decision details, panel marginal status, and oracle isolation proof.
