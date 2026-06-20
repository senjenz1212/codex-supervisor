## test_validate_mergeability_corpus_rejects_task_without_positive_control

Maps to: P1, P7

Public boundary: `validate_mergeability_corpus`

RED: Build a temporary held-out task with only negative controls and assert validation reports a task-specific missing positive control error.

GREEN: Add per-task held-out control validation derived from the manifest candidate metadata.

## test_validate_mergeability_corpus_rejects_task_without_negative_control

Maps to: P1, P7

Public boundary: `validate_mergeability_corpus`

RED: Build a temporary held-out task with only positive controls and assert validation reports a task-specific missing negative control error.

GREEN: Extend the same validation path to require both outcome directions for every included task.

## test_validate_mergeability_corpus_rejects_task_without_false_accept_trap

Maps to: P1, P7

Public boundary: `validate_mergeability_corpus`

RED: Build a temporary held-out task with positive and negative controls but no public-pass hidden-fail trap, then assert validation rejects it.

GREEN: Record false-accept traps from control metadata and require at least one trap per held-out task.

## test_paired_report_separates_heldout_metrics_and_confidence_intervals

Maps to: P2, P4, P7

Public boundary: `run_paired_acceptance_pilot`

RED: Run the paired pilot and assert the report contains a held-out split block separate from dev metrics, plus `n_bad`, `n_good`, denominator, and labeled interval fields for each acceptance arm.

GREEN: Add split metrics and Wilson-style approximate intervals to arm summaries.

## test_no_regression_blocks_prior_true_positive_rejects_not_false_accepts

Maps to: P3, P7

Public boundary: `run_paired_acceptance_pilot`

RED: Run one prior true-positive candidate rejected by the full-gate reviewer panel and one prior false accept. Assert only the true-positive rejection appears in no-regression findings.

GREEN: Scope no-regression to baseline-accepted oracle-positive rows that the available full gate rejects.

## test_heldout_no_regression_report_remains_non_applyable

Maps to: P6, P7

Public boundary: `run_paired_acceptance_pilot`

RED: Run a no-regression finding through the paired pilot and assert the public report keeps metric application, policy mutation, gate advancement, and policy-proposal derivation disabled.

GREEN: Preserve report-only invariants on the no-regression reporting path and ensure policy proposal derivation returns no applyable changes from calibration evidence.

## test_best_of_k_peak_cannot_be_labeled_heldout_improvement

Maps to: P5, P6, P7

Public boundary: `run_paired_acceptance_pilot`

RED: Inspect the paired report and assert best-of-K or in-sample peak fields are marked non-applyable and cannot become held-out improvement labels.

GREEN: Add an explicit held-out reporting guard block while preserving all report-only invariants.
