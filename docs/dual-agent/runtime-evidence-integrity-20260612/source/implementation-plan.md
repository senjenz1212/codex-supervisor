# Implementation Plan: Runtime-Evidence Integrity

## Files / Modules To Touch

- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_axi.py`
- `supervisor/runtime_evidence.py`
- `supervisor/quality_trends.py`
- `supervisor/lessons.py`
- `tests/test_runtime_evidence.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
- `tests/test_quality_trends.py`
- `tests/test_codex_supervisor_axi.py`
- `tests/test_postgres_ledger_lane.py`
- `tests/test_workflow_gate_instruction.py`
- `tests/test_supervisor_lessons.py`
- `docs/LOOP.md`
- `docs/dual-agent/auto-evolution-loop-proof-20260610/demo-manifest.json`

## Steps

1. Finish `_runtime_baseline_for_gate` and preserve the round-1 execution marker.
2. Pass merged `gate_artifacts` (generated source artifacts plus caller artifacts) into `collect_runtime_evidence`.
3. Add `runtime_tdd_test_coverage` receipts and red reasons for missing/unresolved tests plus skipped-without-reason tests.
4. Surface by-era incident counts with run-denominator rates in `query_quality_trends` and AXI output.
5. Label AXI-origin poll incidents as AXI while preserving MCP defaults.
6. Align workflow test fixtures so generated source TDD/implementation artifacts name the runtime tests they actually execute.
7. Add Postgres parity coverage and live TOON metric emission coverage.
8. Preserve runtime-native probe failure details in next-round corrective context so a TDD coverage failure cannot collapse to generic non-convergence.
9. Add an execution/outcome runtime TDD contract block to lead instructions, derived from the generated source TDD artifact, and require canonical gate decisions so the supervisor runtime floor can rerun declared tests instead of accepting `accept_with_residual`.
10. Strengthen advisory lesson injection so prior lessons cannot become standalone gate blockers without current matching evidence.
11. Update `docs/LOOP.md` and the proof manifest hash.
12. Run focused tests, then full pytest with `/Users/sam.zhang/.local/bin` on PATH.

## Risks

- The TDD floor could ignore generated source artifacts when caller `planning_artifacts` is empty; this would make normal workflow artifacts invisible to runtime evidence.
- Test fixtures with source TDD names must execute matching runtime tests, or the stronger floor should block them.
- File-level pytest commands must prove per-testcase pass/skip status; skipped tests require a recorded reason and must never be miscounted as executed.
- Runtime-native failure details must stay visible across retries; generic non-convergence text is not enough for a corrective lead round.
- Advisory lessons must guide reviewer attention without replacing the current gate evidence or creating self-fulfilling FM-1.3 blocks.
- Era trend rates must use a run denominator, not incident-share-of-total; share can be reported separately.
- Trend rows must stay read-only; adding aggregation fields must not recompute or mutate history during query.
- AXI poll failure should not make poll execute phases inline.

## Traceability

- P1 -> `test_runtime_baseline_execution_round_one_persists_marker`, `test_runtime_baseline_execution_later_rounds_do_not_overwrite_marker`, `test_runtime_baseline_outcome_review_reuses_persisted_marker_across_api_instance`, `test_runtime_baseline_outcome_review_missing_marker_falls_back_fresh`, `test_runtime_baseline_marker_write_failure_visible_as_outcome_fallback`
- P2 -> `test_runtime_evidence_fails_when_tdd_named_tests_are_not_executed`, `test_runtime_evidence_does_not_count_pytest_filtered_out_tdd_names`, `test_runtime_evidence_does_not_count_skipped_pytest_tdd_names`, `test_runtime_evidence_does_not_count_explicit_skipped_nodeid_as_executed`, `test_runtime_evidence_fails_when_skipped_tdd_name_lacks_reason`, `test_execution_gate_uses_generated_source_tdd_for_runtime_coverage`, `test_runtime_evidence_accepts_when_all_tdd_named_tests_execute`, `test_runtime_evidence_fails_when_tdd_test_name_is_unresolved`, `test_read_gate_transcript_includes_skill_receipt_validation`, `test_workflow_round_objection_preserves_runtime_probe_details`, `test_execution_gate_instruction_includes_tdd_runtime_contract`, `test_workflow_tdd_test_names_reads_tdd_artifacts`
- P3 -> `test_axi_trends_surfaces_by_era_in_json_and_toon`, `test_axi_trends_uses_legacy_incident_eras_as_denominators`
- P4 -> `test_transport_incident_events_are_written_from_public_axi_and_mcp_poll_failure_boundaries`
- P5 -> `test_postgres_trends_details_and_incident_aggregation_match_sqlite`
- P6 -> `test_axi_toon_poll_records_format_metric`
- P7 -> `test_lesson_injection_says_lessons_are_not_standalone_gate_decisions`
