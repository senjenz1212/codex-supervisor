## Public Boundary First

The first RED test exercises swebench_mergeability_official_all_arms_diagnostic_runner with official-shaped dataset records, produced baseline receipts, a fake official-equivalent oracle adapter, and a configured-style reviewer panel. Helper-only tests are deferred because the promise is about the report an operator receives.

## RED/GREEN Cycles

### test_official_all_arms_diagnostic_completes_with_matched_tar_and_no_claim

Maps to: Slice 1, Slice 2, Slice 3, P1, P3, P4

Red: The public runner lacks a completed all-arms diagnostic report with n_good, n_bad, matched-TAR status, FAR/TAR/FRR, hidden leak proof, and non-applyable flags.

Green: Add the wrapper report and top-level fields while reusing official replay for dataset loading, public bundle materialization, decision freeze, and oracle receipts.

### test_official_all_arms_diagnostic_is_unavailable_when_oracle_is_unavailable

Maps to: Slice 1, Slice 2, P2, P4

Red: An oracle adapter unavailable row can still leave a diagnostic report that appears measurable.

Green: Treat official replay unavailable status and bridge metric suppression as diagnostic unavailable reasons, and omit FAR/TAR/FRR from the top-level signal.

### test_official_all_arms_diagnostic_cli_writes_unavailable_report_without_panel

Maps to: Slice 1, Slice 2, P1, P2, P4

Red: The new CLI flag can route to regular official replay or fail to persist the diagnostic report path.

Green: Route --official-all-arms-diagnostic into the wrapper, write official_all_arms_diagnostic_report.json, and summarize unavailable status when no configured panel exists.

### test_official_all_arms_diagnostic_refuses_claim_when_full_gate_unavailable

Maps to: Slice 1, Slice 2, P1, P2, P4

Red: A run with no full reviewer roster can be confused with an S_full quality result.

Green: Require S_full arm availability and configured reviewer-panel full roster evidence before diagnostic completion.

### test_official_all_arms_diagnostic_refuses_claim_when_baseline_unavailable

Maps to: Slice 1, Slice 2, P1, P2, P4

Red: A missing produced single-agent baseline receipt can fall back to accept-all style metadata.

Green: Require produced baseline availability and let matched-TAR report baseline_arm_unavailable rather than computing a comparison.

### test_official_all_arms_diagnostic_blocks_hidden_field_leak

Maps to: Slice 1, Slice 2, P2, P4

Red: A public artifact containing a forbidden SWE-bench hidden-oracle marker can leave the diagnostic looking measurable.

Green: Surface hidden_field_leak_check at the diagnostic level, mark the run unavailable, and preserve no-claim flags.

## Regression Sweep

Run the focused diagnostic tests first, then the full SWE-bench mergeability bridge test file. Because shared matched-TAR and baseline logic is imported from mergeability_bench, also rerun the focused mergeability baseline and matched-TAR subset after the bridge tests are green.
