# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `supervisor/autoresearch/evaluators/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `tests/fixtures/mergeability_bench/tasks/calculator_addition.json`
- `tests/fixtures/mergeability_bench/repos/calculator_bug/app/calculator.py`
- `tests/fixtures/mergeability_bench/repos/calculator_bug/hidden/test_behavior.py`
- `tests/fixtures/mergeability_bench/candidates/noop.json`
- `tests/fixtures/mergeability_bench/candidates/known_bad.json`
- `tests/fixtures/mergeability_bench/candidates/known_good.json`

## Risks

- The evaluator could become another shallow fixture replay if no-op and known-bad controls are not enforced.
- Generic AutoResearch evaluator quality controls could regress if mergeability-specific candidate discovery leaks into unrelated evaluators.
- Runtime-native receipts could be emitted by the script but not remain replayable if result hashes include nondeterministic command timing.

## Traceability

P1 maps to test_load_mergeability_tasks_reads_typed_fixture_contract.
P2 maps to test_mergeability_controls_discriminate_noop_known_bad_and_known_good.
P2 also maps to test_reverse_classical_rejects_candidate_without_submitted_tests.
P3 maps to test_autoresearch_mergeability_evaluator_emits_computed_runtime_native_metric.
P4 maps to test_mergeability_controls_discriminate_noop_known_bad_and_known_good.
