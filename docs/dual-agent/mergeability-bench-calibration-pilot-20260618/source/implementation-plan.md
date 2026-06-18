# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `tests/fixtures/mergeability_bench/candidates/known_good.json`
- `tests/fixtures/mergeability_bench/candidates/known_bad.json`
- `tests/fixtures/mergeability_bench/candidates/noop.json`
- `docs/dual-agent/mergeability-bench-calibration-pilot-20260618/source/prd.md`
- `docs/dual-agent/mergeability-bench-calibration-pilot-20260618/source/tdd.md`

## Implementation Steps

1. Add manifest and calibration helpers around the existing deterministic mergeability grader.
2. Expand the local candidate fixture pool to twelve control cases over the existing calculator task.
3. Add a paired acceptance pilot that evaluates baseline and Supervisor arms over the same candidate ids.
4. Export corpus manifest, calibration summary, paired report, and per-task JSONL artifacts.
5. Keep all outputs report-only and avoid any policy proposal or gate mutation side effect.

## Traceability

P1 maps to test_mergeability_corpus_manifest_requires_positive_and_negative_controls.
P2 maps to test_mergeability_calibration_covers_seeded_failure_modes.
P3 maps to test_paired_acceptance_pilot_reports_baseline_false_accept_and_supervisor_rejection.
P4 maps to test_paired_acceptance_pilot_exports_replayable_artifacts.

## Risks

- The pilot is intentionally not a production accuracy proof and should not be presented as a full multi-model benchmark.
- The local corpus is seeded for verifier behavior, so later powered experiments still need broader tasks and generator controls.
