# TDD Plan

## test_fixture_runner_missing_produced_baseline_receipt_is_unavailable

Maps to: P1, P2, Slice 1.

RED: run the fixture runner with legacy `baseline_self_report=true` but no produced receipt, then expect the bridge row to mark baseline unavailable.

GREEN: replace the runner fallback with the produced-baseline normalizer and preserve legacy self-report only as calibration context.

## test_fixture_runner_produced_baseline_receipt_populates_baseline_arm

Maps to: P1, P3, Slice 1.

RED: run the fixture runner with a full produced receipt and expect baseline provenance fields and summary evidence to be available.

GREEN: thread normalized receipt fields through frozen decisions, bridge rows, replay aggregation, and arm summaries.

## test_bridge_legacy_bool_baseline_row_is_unavailable

Maps to: P1, P2, Slice 1.

RED: call the bridge with a legacy boolean baseline row and expect the baseline arm to be unavailable with a legacy evidence reason.

GREEN: normalize direct bridge baseline rows through the same produced-baseline validator.

## test_live_runner_without_baseline_decision_receipts_does_not_synthesize_accept

Maps to: P2, P3, Slice 2.

RED: run the live runner with fake generators returning patches but no accept or reject receipts, then expect baseline unavailable.

GREEN: attach live produced baseline receipts only when generator output includes an explicit accept or reject decision.
