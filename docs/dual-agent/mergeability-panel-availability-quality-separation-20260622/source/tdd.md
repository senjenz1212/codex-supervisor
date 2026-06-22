## Public Boundary First

test_missing_reviewer_verdict_blocks_production_but_labels_missing_block

Maps to: P1

Red: Run the mergeability calibration boundary with a panel result missing an expected reviewer verdict and assert the production/full gate is unavailable or revise while the measurement label is panel_missing_verdict_block.

Green: Add row-level availability classification that separates missing verdict blocks from quality rejection without changing acceptance authority.

test_fully_available_rejecting_panel_counts_as_quality_reject

Maps to: P2

Red: Run the mergeability report with a complete reviewer roster that rejects a candidate and assert the row is full_roster_available with panel_quality_reject.

Green: Preserve the existing rejection behavior while attaching quality labels only when every required reviewer verdict is present.

test_panel_marginal_refuses_when_no_full_roster_rows

Maps to: P3

Red: Build an aggregate report where every S_full row is infrastructure unavailable and assert panel marginal FAR/TAR is unavailable with an insufficient full-roster reason.

Green: Filter panel marginal computation to fully available rows and expose unavailable status when the denominator is zero.

test_reviewer_zero_infrastructure_failure_records_diagnostic

Maps to: P4

Red: Simulate reviewer-0 infrastructure failure and assert diagnostics include runtime, failure class, recoverability, and transcript or receipt hash evidence without hidden oracle content.

Green: Thread reviewer infrastructure diagnostics from configured panel execution into the mergeability measurement report.

test_codex_only_calibration_is_labeled_and_not_full_panel

Maps to: P5

Red: Run Codex-only calibration mode and assert the report says codex_only_calibration, refuses full-panel labeling, and does not create policy proposals.

Green: Add explicit partial-roster calibration labeling and block promotion surfaces from consuming it as full-panel evidence.
