# Implementation Plan

## Files / Modules To Touch

- `supervisor/mergeability_bench.py`
- `tests/test_mergeability_bench.py`
- `tests/fixtures/mergeability_bench/candidates/*.json`
- `tests/fixtures/mergeability_bench/tasks/*.json`

## Generated Evidence Artifacts

- `.scratch/mergeability-fixture-diagnostic-corpus-growth-20260621/paired_acceptance_report.json`
- `.scratch/mergeability-fixture-diagnostic-corpus-growth-20260621/corpus_manifest.json`
- `.scratch/mergeability-fixture-diagnostic-corpus-growth-20260621/calibration_summary.json`

## Steps

1. Inspect the Slice 1A persisted report and derive the diagnostic growth reason from its measured rates: S_probe TAR 1.0, S_full TAR 0.0, and full-gate matched TAR not matched.
2. Add diagnostic oracle-positive fixture candidates under `tests/fixtures/mergeability_bench/candidates/` in the existing mergeability corpus format, preserving hidden oracle files, mutable path policy, produced baseline compatibility, and current negative traps.
3. Extend manifest and report assembly only where needed to record growth rationale, retained control coverage, n_good, n_bad, S_probe/S_full false accepts, TAR loss, matched-TAR status, and confidence intervals.
4. Re-run the fixture panel produced-baseline measurement and persist the updated calibration artifact for this slice.
5. Keep every output report-only: no policy mutation, no applyable metric, no default change, and no improvement claim.

## Risks

- Adding positives can make rates look healthier without improving detection, so the report must keep separate true-accept and false-accept denominators.
- The panel can remain too conservative even with more positives, so the output must treat not-matched status as a diagnostic finding rather than a failure to hide.
- New fixture metadata can leak hidden oracle material if public packets are not rebuilt through the existing leak detector.

## Traceability

- P1 and P2 are covered by `test_fixture_diagnostic_corpus_growth_reports_slice1a_positive_denominator`.
- P3 is covered by `test_fixture_diagnostic_corpus_growth_excludes_hidden_oracle_material`.
- P2 is covered by `test_fixture_diagnostic_corpus_growth_preserves_controls_and_false_accept_traps`.
- P4 is covered by `test_fixture_diagnostic_report_exports_tar_loss_and_confidence_intervals`.
- P5 is covered by `test_fixture_diagnostic_report_stays_calibration_only`.
