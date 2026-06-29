# TDD Plan

Task id: `powered-real-benchmark-run-20260626`

## Public Boundary

The first RED test targets the new checker interface: `assert_powered_real_benchmark_definition_of_done(powered_report=..., all_arms_diagnostic_report=...)`. This is the public boundary because the real scaled run is execution evidence; unit tests should validate the Definition of Done, not pretend to execute Docker, Pro oracle, live model generation, or a cross-family panel.

## One RED Then Minimal GREEN

RED

`tests/test_powered_real_benchmark_dod.py::test_artifact_is_powered` imports the checker and feeds a complete report-shaped artifact. It initially failed with `ModuleNotFoundError: No module named 'supervisor.powered_real_benchmark'`.

Minimal GREEN

Add `supervisor/powered_real_benchmark.py` with one public assertion function and a structured verdict. The minimal implementation checks raw sample-size sufficiency, paired `full_supervisor_stack` McNemar power, source provenance, and the evidence-conversion contract.

## Next Cycles

### Cycle 2: FAR/TAR/FRR Confidence Intervals

RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_far_and_wilson_present`

GREEN: require `far_tar_frr` for `baseline`, `s_probe`, `s_full`, and `oracle_ceiling`, with non-empty confidence-interval blocks and positive denominators.

### Cycle 3: Honest Report-Only Proxy

RED: `tests/test_powered_real_benchmark_dod.py::test_artifact_is_report_only_test_pass_proxy`

GREEN: require the held-out test-pass proxy label, no maintainer mergeability claim, all authority flags false, and policy mutation disabled.

### Cycle 4: Fail Closed

RED: `tests/test_powered_real_benchmark_dod.py::test_underpowered_artifact_is_rejected`

GREEN: raise `PoweredRealBenchmarkDoDError` with explicit reason codes for underpowered sample evidence.

## Refactor Check

- Keep the checker as a deep module with one public seam.
- Do not fork McNemar or Wilson math; validate the fields emitted by existing runners.
- Do not call live infrastructure from unit tests.
- Do not add any policy bridge or authority mutation.

## First RED Evidence

```text
ERROR tests/test_powered_real_benchmark_dod.py
E   ModuleNotFoundError: No module named 'supervisor.powered_real_benchmark'
```
