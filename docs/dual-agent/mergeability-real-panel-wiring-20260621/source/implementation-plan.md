# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `supervisor/reviewer_registry.py`
- `tests/test_mergeability_bench.py`
- `docs/dual-agent/mergeability-real-panel-wiring-20260621/source/prd.md`
- `docs/dual-agent/mergeability-real-panel-wiring-20260621/source/tdd.md`

## Risks

- Reviewer infrastructure can be slow or unavailable, so default tests must inject fake reviewers below the registry seam.
- A new adapter can accidentally duplicate conservative panel aggregation instead of reusing the existing reviewer registry behavior.
- Packet serialization can leak hidden oracle material unless the leak detector runs before reviewer invocation.
- Calibration reports can be misread as policy authority unless report-only invariants are asserted at the public boundary.

## Traceability

- P1 -> test_run_paired_acceptance_pilot_uses_configured_panel_when_requested
- P2 -> test_configured_panel_not_invoked_when_reviewer_packet_contains_oracle_material
- P3 -> test_configured_panel_unavailable_does_not_count_as_accept
- P3 -> test_configured_panel_calibration_remains_report_only
- P4 -> test_configured_panel_report_records_reviewer_results_and_packet_refs

## Steps

1. Add the configured reviewer-panel adapter with injected reviewer adapters and runner dependencies.
2. Wire explicit configured-panel mode into `run_paired_acceptance_pilot` while preserving custom panel callables.
3. Add oracle-leak blocking before reviewer invocation and unavailable normalization for missing verdicts.
4. Record reviewer result summaries, panel decisions, and packet references in rows and arm summaries.
5. Run the targeted mergeability tests and the supervisor workflow gates through outcome review.
