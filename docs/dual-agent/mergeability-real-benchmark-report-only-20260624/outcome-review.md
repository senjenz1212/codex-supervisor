# Outcome Review

Status: accepted.

## Summary

The official all-arms diagnostic now emits report-only real-benchmark evidence fields without changing policy authority. The report states the SWE-bench oracle limitation, exposes decision-freeze evidence, carries S_probe-vs-S_full and reviewer marginal fields, and includes reviewer provenance, rubric/abstention coverage, generator disjointness, and self-preference warnings.

## Verification

- `uv run pytest tests/test_swe_bench_pro_mergeability_bridge.py` passed with 81 tests.
- `uv run pytest tests/test_runtime_evidence.py` passed with 23 tests.
- `git diff --check -- supervisor/swe_bench_mergeability.py tests/test_swe_bench_pro_mergeability_bridge.py docs/dual-agent/mergeability-real-benchmark-report-only-20260624` passed.

## Report-Only Guard

The implementation preserves `metric_applyable=false`, `improvement_claim_allowed=false`, `powered_improvement_claim_allowed=false`, `human_mergeability_claim_allowed=false`, `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

## Gate Notes

AXI and no-mistakes wrappers are not available on this shell path, so this slice is validated with runtime-native pytest and diff hygiene evidence.
