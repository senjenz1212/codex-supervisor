## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `tests/fixtures/mergeability_bench/candidates/text_hidden_behavior_miss.json`

## Plan

1. Extend mergeability manifest construction with per-task held-out requirements for positive controls, negative controls, and public-pass hidden-fail traps.
2. Add a text-processing false-accept trap candidate so each default held-out task has a trap, not only the arithmetic task.
3. Preserve candidate grading for positive-and-negative subsets even when the full held-out control policy rejects the manifest.
4. Change no-regression findings to protect baseline-accepted oracle-positive rows rejected by an available full gate, while allowing prior oracle-negative false accepts to be rejected.
5. Add split reporting, best-of-K guardrails, and Wilson-style approximate interval fields to paired acceptance reports.
6. Prove the behavior through public-boundary tests on `validate_mergeability_corpus` and `run_paired_acceptance_pilot`, then run the full mergeability bench test file.

## Risks

- Tightening manifest validation can accidentally suppress grading for intentionally broken control subsets, hiding the more specific calibration failure.
- Default reviewer-panel unavailability can look like a no-regression failure unless the rule explicitly requires an available full-gate decision.
- New report fields can be misread as improvement authority unless report-only flags and policy derivation guards remain covered.

## Traceability

- P1 -> `test_validate_mergeability_corpus_rejects_task_without_positive_control`, `test_validate_mergeability_corpus_rejects_task_without_negative_control`, `test_validate_mergeability_corpus_rejects_task_without_false_accept_trap`
- P2 -> `test_paired_report_separates_heldout_metrics_and_confidence_intervals`
- P3 -> `test_no_regression_blocks_prior_true_positive_rejects_not_false_accepts`
- P4 -> `test_paired_report_separates_heldout_metrics_and_confidence_intervals`
- P5 -> `test_best_of_k_peak_cannot_be_labeled_heldout_improvement`
- P6 -> `test_best_of_k_peak_cannot_be_labeled_heldout_improvement`, `test_heldout_no_regression_report_remains_non_applyable`
- P7 -> every named test above exercises `validate_mergeability_corpus` or `run_paired_acceptance_pilot`
