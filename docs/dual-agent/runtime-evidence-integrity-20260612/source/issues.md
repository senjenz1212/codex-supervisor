# Issues: Runtime-Evidence Integrity

## Slice 1: Baseline marker lifecycle

Scope: Persist execution round-1 baseline markers, preserve later execution fresh baselines without overwrite, and reuse the marker during outcome review.

PRD promise: P1

Acceptance Criteria:

- [ ] `test_runtime_baseline_execution_round_one_persists_marker` proves round-1 marker creation.
- [ ] `test_runtime_baseline_execution_later_rounds_do_not_overwrite_marker` proves later rounds preserve the marker.
- [ ] `test_runtime_baseline_outcome_review_reuses_persisted_marker_across_api_instance` proves restart-safe reuse.
- [ ] `test_runtime_baseline_outcome_review_missing_marker_falls_back_fresh` proves visible fallback.
- [ ] `test_runtime_baseline_marker_write_failure_visible_as_outcome_fallback` proves marker write failure remains visible later.

Priority: P1

## Slice 2: TDD execution coverage floor

Scope: Add runtime-native evidence that every TDD-named test was resolved and supervisor-executed.

PRD promise: P2

Acceptance Criteria:

- [ ] `test_runtime_evidence_fails_when_tdd_named_tests_are_not_executed` produces red `tdd_tests_not_executed`.
- [ ] `test_runtime_evidence_does_not_count_pytest_filtered_out_tdd_names` proves filtered pytest file targets cannot over-credit every TDD-named test in that file.
- [ ] `test_execution_gate_uses_generated_source_tdd_for_runtime_coverage` proves generated `source/tdd.md` artifacts are part of the runtime floor even when caller `planning_artifacts` is empty.
- [ ] `test_runtime_evidence_accepts_when_all_tdd_named_tests_execute` keeps P11 green.
- [ ] `test_runtime_evidence_fails_when_tdd_test_name_is_unresolved` produces red `tdd_test_names_unresolved`.
- [ ] `test_read_gate_transcript_includes_skill_receipt_validation` keeps the existing MCP transcript path green with aligned source TDD/implementation fixtures under the stronger floor.

Priority: P1

## Slice 3: Trend visibility and format metrics

Scope: Surface by-era trend data and prove live TOON-lite poll emits format metrics.

PRD promises: P3, P6

Acceptance Criteria:

- [ ] `test_axi_trends_surfaces_by_era_in_json_and_toon` asserts JSON and TOON-lite era output.
- [ ] `test_axi_toon_poll_records_format_metric` asserts live default poll emits a TOON metric event.

Priority: P2

## Slice 4: Poll failure public boundaries

Scope: Verify AXI and MCP missing-job polling write transport incident events from public surfaces.

PRD promise: P4

Acceptance Criteria:

- [ ] `test_transport_incident_events_are_written_from_public_axi_and_mcp_poll_failure_boundaries` covers AXI and MCP missing-job polls.

Priority: P2

## Slice 5: Postgres trend parity

Scope: Prove trend details and incident aggregation behave on the Postgres lane.

PRD promise: P5

Acceptance Criteria:

- [ ] `test_postgres_trends_details_and_incident_aggregation_match_sqlite` roundtrips detail JSON and aggregates incidents through `read_events_since`.

Priority: P2
