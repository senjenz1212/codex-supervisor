# TDD Plan

## Public Boundary

Use `run_paired_acceptance_pilot` as the first public boundary. Tests may inject fake reviewer adapters below the reviewer registry seam, but they must exercise the same report rows and arm summaries operators inspect.

## Test Cases

### test_run_paired_acceptance_pilot_uses_configured_panel_when_requested

Maps to: ISS-1, P1, P4
RED: Call `run_paired_acceptance_pilot` with configured-panel mode and fake configured reviewers, then assert the current implementation still reports `reviewer_panel_not_configured`.
GREEN: Add the configured reviewer-panel adapter and wire it into the full-gate path when requested.

### test_configured_panel_unavailable_does_not_count_as_accept

Maps to: ISS-1, P3
RED: Provide fake reviewers with no usable verdict and assert S_full currently risks collapsing into an unclear fallback.
GREEN: Normalize missing or infrastructure-failed reviewer results to unavailable, set the reviewer-panel unavailable gaming flag, and keep S_full accept false.

### test_configured_panel_not_invoked_when_reviewer_packet_contains_oracle_material

Maps to: ISS-2, P2, P3
RED: Force a forbidden oracle marker into the reviewer packet and assert the fake reviewer runner must not be called.
GREEN: Run `_public_input_oracle_refs` before invoking configured reviewers and return an oracle-isolation failure result when leaks exist.

### test_configured_panel_report_records_reviewer_results_and_packet_refs

Maps to: ISS-2, ISS-3, P4
RED: Run configured-panel calibration and assert rows lack durable reviewer result summaries from the registry path.
GREEN: Record independent reviewer results, panel decision, reviewer packet refs, packet hash, and S_probe versus S_full disagreement in the report.

### test_configured_panel_calibration_remains_report_only

Maps to: ISS-2, P3
RED: Ask policy evolution to derive a proposal from the configured-panel calibration report and assert no applyable proposal is produced.
GREEN: Preserve `metric_applyable=false`, `improvement_claim_allowed=false`, `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

## RED/GREEN Plan

RED: Add one public-boundary test proving configured-panel mode still falls back to unavailable behavior.
GREEN: Add the smallest adapter that invokes injected configured reviewers and aggregates their results.

RED: Add one public-boundary test for missing reviewer verdicts.
GREEN: Normalize missing reviewer outputs into unavailable S_full measurements.

RED: Add one public-boundary test for oracle leak blocking before reviewer invocation.
GREEN: Reuse the existing leak detector at the adapter entry and return a blocked full-gate review.

RED: Add one public-boundary test for replayable reviewer evidence and report-only invariants.
GREEN: Add stable report fields and keep calibration non-applyable.
