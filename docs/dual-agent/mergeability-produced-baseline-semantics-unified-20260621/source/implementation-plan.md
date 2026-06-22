# Implementation Plan

## Files / Modules To Touch

- `supervisor/swe_bench_mergeability.py`
- `tests/test_swe_bench_pro_mergeability_bridge.py`
- `docs/dual-agent/mergeability-produced-baseline-semantics-unified-20260621/source/tdd.md`

## Steps

1. Import the existing produced-baseline receipt normalizer into the SWE-bench mergeability bridge.
2. Normalize baseline rows in direct bridge reports using candidate id and candidate artifact hash.
3. Replace the fixture runner's `baseline_self_report` fallback with explicit produced receipt extraction.
4. Persist baseline provenance through frozen decisions, per-row bridge reports, and replay aggregation.
5. Preserve legacy self-report only as calibration context.
6. Prevent live generation from adding baseline acceptance unless the generator emits an explicit accept or reject decision.
7. Verify targeted nodeids, the SWE-bench mergeability bridge suite, local produced-baseline nodeids, py_compile, and diff checks.

## Risks

- The replay path could accidentally re-normalize an already unavailable receipt and lose the original missing or malformed evidence kind.
- The live path could infer baseline acceptance from successful patch generation, recreating the accept-all problem under a new name.
- Existing fixture tests could be made green by helper-only changes unless the runner and report boundaries stay under test.

## Traceability

P1 is covered by `test_fixture_runner_produced_baseline_receipt_populates_baseline_arm` and `test_bridge_legacy_bool_baseline_row_is_unavailable`.

P2 is covered by `test_fixture_runner_missing_produced_baseline_receipt_is_unavailable` and `test_live_runner_without_baseline_decision_receipts_does_not_synthesize_accept`.

P3 is covered by `test_fixture_runner_produced_baseline_receipt_populates_baseline_arm` and `test_live_runner_without_baseline_decision_receipts_does_not_synthesize_accept`.
