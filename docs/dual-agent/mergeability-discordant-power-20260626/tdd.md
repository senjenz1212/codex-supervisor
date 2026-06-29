# TDD Plan

The implementation follows one RED, one minimal GREEN, then repeats. The first RED is at the public report boundary.

## Cycle 1

RED: `tests/test_discordant_power.py::test_all_concordant_rows_report_underpowered`

Boundary: `run_powered_factorial_mergeability_evaluation`

Expected failure before implementation: the report lacks `paired_power`, and `metric_applyable` can be true when raw thresholds are met but paired discordance is zero.

Minimal GREEN: add `_paired_power_sufficiency`, emit `paired_power`, and require paired-power sufficiency before `metric_applyable` can be true.

## Cycle 2

RED: `tests/test_discordant_power.py::test_exact_binomial_used_under_25_discordant`

Boundary: `run_powered_factorial_mergeability_evaluation`

Expected failure before implementation: sparse nonzero paired discordance has no exact-binomial method or p-value and is not guaranteed to keep `metric_applyable=False`.

Minimal GREEN: use exact two-sided binomial McNemar under the configured discordant threshold and keep sparse discordance underpowered.

## Cycle 3

RED: `tests/test_discordant_power.py::test_continuity_corrected_mcnemar_used_at_threshold`

Boundary: `run_powered_factorial_mergeability_evaluation`

Expected failure before implementation: threshold-reaching paired discordance has no continuity-corrected McNemar chi-square method, statistic, p-value, alpha, or pass criterion.

Minimal GREEN: add the continuity-corrected chi-square branch for `discordant_n >= min_discordant`.

## Cycle 4

RED: `tests/test_discordant_power.py::test_far_zero_reports_rule_of_three_upper_bound`

Boundary: powered factorial arm summary at the public report boundary.

Expected failure before implementation: zero-event FAR, TAR, and FRR use the ordinary Wilson upper bound instead of a rule-of-three upper bound.

Minimal GREEN: update `_wilson_interval` so count `0/n` reports lower `0.0`, rule-of-three upper bound, and interval provenance.

## Cycle 5

RED: `tests/test_discordant_power.py::test_rates_carry_wilson_intervals`

Boundary: powered factorial arm summaries and `far_tar_frr` projections.

Expected failure before implementation: FRR can be missing interval projection, compact rate summaries are absent, and interval provenance may not prove non-Wald math.

Minimal GREEN: add shared rate interval summaries for FAR/TAR/FRR, project the FRR interval into `far_tar_frr`, and assert method metadata stays Wilson or rule-of-three.

## Regression Scope

- `tests/test_discordant_power.py`
- Existing powered factorial tests in `tests/test_mergeability_bench.py`
- Existing SWE-bench `far_tar_frr` tests in `tests/test_swe_bench_pro_mergeability_bridge.py`

## Mocking Boundary

Fixture decisions are supplied below `run_powered_factorial_mergeability_evaluation`. The test does not mock `_paired_power_sufficiency`, `_paired_discordant_counts`, or report assembly.
