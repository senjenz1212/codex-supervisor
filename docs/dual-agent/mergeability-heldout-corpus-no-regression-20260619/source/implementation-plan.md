## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `tests/fixtures/mergeability_bench/tasks/*.json`
- `tests/fixtures/mergeability_bench/candidates/*.json`
- `tests/fixtures/mergeability_bench/repos/*`

## Approach

Extend the existing mergeability task and candidate payloads with optional split, task_class, and no-regression metadata while preserving defaults for existing fixtures. Add at least one additional deterministic fixture task class with passing and failing controls. Compute task_class coverage and no-regression findings during the paired report path, export stable hashes, and keep policy derivation blocked by the existing report-only invariants.

## Risks

- Adding fixture breadth can slow tests if every candidate invokes many subprocesses; keep fixtures compact.
- No-regression can be gamed if it only checks candidate-submitted tests; use hidden or reverse commands already owned by the task.
- Report fields can accidentally become applyable evidence; preserve all authority flags and policy-evolution guards.

## Traceability

P1 maps to `test_paired_report_records_heldout_task_class_coverage`.

P2 maps to `test_validate_mergeability_corpus_requires_controls_per_task_class`.

P3 maps to `test_paired_report_catches_no_regression_failure`.

P4 maps to `test_heldout_no_regression_report_remains_non_applyable`.

P1 and P3 replayability map to `test_no_regression_and_heldout_artifacts_export_replayable_hashes`.
