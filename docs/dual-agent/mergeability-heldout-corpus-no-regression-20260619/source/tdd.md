## test_paired_report_records_heldout_task_class_coverage

Maps to: P1, P5

Public boundary: `run_paired_acceptance_pilot`

RED: Run the paired pilot over the fixture corpus and assert that the returned public report contains held-out split metadata plus per-task_class candidate/control coverage. This fails today because the report has no held-out coverage block.

GREEN: Add task split and task_class metadata to task payloads, manifest entries, and paired reports while keeping hidden oracle data out of public report fields.

## test_validate_mergeability_corpus_requires_controls_per_task_class

Maps to: P2, P5

Public boundary: `validate_mergeability_corpus`

RED: Build a temporary corpus with one task_class that has only passing controls and assert `validate_mergeability_corpus` rejects it with a per-task_class missing-negative-control finding. This fails today because validation does not enforce per-class control coverage.

GREEN: Validate held-out task classes independently and reject any class missing a positive or negative control.

## test_paired_report_catches_no_regression_failure

Maps to: P3, P5

Public boundary: `run_paired_acceptance_pilot`

RED: Run a candidate that improves one visible behavior while breaking a previously passing control behavior and assert the paired report records a blocking no-regression failure for that candidate. This fails today because no-regression findings are not computed or surfaced.

GREEN: Add no-regression computation to candidate grading/report rows and report-level summaries.

## test_heldout_no_regression_report_remains_non_applyable

Maps to: P4, P5

Public boundary: `run_paired_acceptance_pilot` plus policy derivation guard

RED: Feed a held-out no-regression calibration report into policy derivation and assert no applyable proposal is produced while all report-only flags remain false. This fails if the new report path weakens the existing policy-evolution guard.

GREEN: Preserve all report-only flags and assert policy derivation returns no proposals.

## test_no_regression_and_heldout_artifacts_export_replayable_hashes

Maps to: P1, P3, P4

Public boundary: `run_paired_acceptance_pilot`

RED: Export paired artifacts and assert deterministic hashes tie task_class coverage and no-regression findings to the report manifest. This fails today because those specific replayable hash fields do not exist.

GREEN: Include deterministic hashes for held-out coverage and no-regression findings in the report and exported JSON files.
