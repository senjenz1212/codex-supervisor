## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `mcp_tools/codex_supervisor_stdio.py`, only if reviewer-0 diagnostics require plumbing from configured reviewer execution

## Risks

- The largest risk is accidentally weakening the production gate while making measurement labels more nuanced.
- Panel marginal math can look valid with an empty full-roster denominator unless the report refuses explicitly.
- Codex-only calibration is useful for smoke diagnostics but dangerous if it can be mistaken for full-panel evidence.
- Reviewer infrastructure diagnostics must not include task oracle fields, hidden tests, or protected path content.

## Traceability

P1 is covered by test_missing_reviewer_verdict_blocks_production_but_labels_missing_block.
P2 is covered by test_fully_available_rejecting_panel_counts_as_quality_reject.
P3 is covered by test_panel_marginal_refuses_when_no_full_roster_rows.
P4 is covered by test_reviewer_zero_infrastructure_failure_records_diagnostic.
P5 is covered by test_codex_only_calibration_is_labeled_and_not_full_panel.
