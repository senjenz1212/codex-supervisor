# TDD Plan: Runtime-Evidence Integrity

## Public Boundary

The first tests hit the same surfaces operators use: workflow runtime baseline helpers, `collect_runtime_evidence`, AXI CLI, MCP poll, and the Postgres state lane.

## Test Cases

### test_runtime_baseline_execution_round_one_persists_marker

Maps to: P1
RED: execution round 1 does not write `runtime-baseline-execution.json`.
GREEN: write the marker with head, captured_at, and task_id.

### test_runtime_baseline_execution_later_rounds_do_not_overwrite_marker

Maps to: P1
RED: execution round 2 overwrites the round-1 marker.
GREEN: capture fresh evidence while leaving the persisted marker unchanged.

### test_runtime_baseline_outcome_review_reuses_persisted_marker_across_api_instance

Maps to: P1
RED: `outcome_review` calls fresh capture despite an existing marker.
GREEN: read the persisted marker and return reason `persisted_execution_baseline`.

### test_runtime_baseline_outcome_review_missing_marker_falls_back_fresh

Maps to: P1
RED: missing marker fails opaquely.
GREEN: fallback to fresh capture with reason `fresh_fallback_no_persisted_execution_baseline`.

### test_runtime_baseline_marker_write_failure_visible_as_outcome_fallback

Maps to: P1
RED: marker write failure is swallowed with no later receipt evidence.
GREEN: the eventual outcome runtime baseline receipt carries the fallback reason.

### test_runtime_evidence_fails_when_tdd_named_tests_are_not_executed

Maps to: P2
RED: TDD names two tests while runtime evidence executes one and still passes.
GREEN: emit red `tdd_tests_not_executed` with missing nodeids.

### test_runtime_evidence_does_not_count_pytest_filtered_out_tdd_names

Maps to: P2
RED: TDD names two tests while runtime evidence runs `pytest tests/test_tdd_floor.py -k test_tdd_floor_one` and credits the whole file.
GREEN: pytest JUnit evidence credits only the nodeid that actually passed and still emits `tdd_tests_not_executed` for the filtered-out TDD nodeid.

### test_runtime_evidence_does_not_count_skipped_pytest_tdd_names

Maps to: P2
RED: a pytest target that returns 0 with `1 skipped` is credited as executed TDD coverage without recording why it skipped.
GREEN: skipped pytest nodeids are recorded separately; skipped nodeids with a non-empty reason satisfy the TDD coverage floor without being counted as executed.

### test_runtime_evidence_does_not_count_explicit_skipped_nodeid_as_executed

Maps to: P2
RED: a skipped explicit pytest nodeid has no passed JUnit cases, falls back to command-target expansion, and is credited as executed.
GREEN: skipped-only JUnit status suppresses command-target fallback; a recorded skip reason covers the nodeid without adding it to executed nodeids.

### test_runtime_evidence_fails_when_skipped_tdd_name_lacks_reason

Maps to: P2
RED: a TDD-named pytest nodeid can be skipped with no meaningful reason and still satisfy the coverage floor.
GREEN: skipped TDD nodeids without a recorded reason remain missing and emit `tdd_tests_skipped_without_reason`.

### test_execution_gate_uses_generated_source_tdd_for_runtime_coverage

Maps to: P2
RED: the workflow has generated `source/tdd.md` names but passes no caller `planning_artifacts`, so runtime evidence never emits `runtime_tdd_test_coverage`.
GREEN: execution/outcome runtime evidence uses merged `gate_artifacts`, including generated source TDD, and blocks filtered partial execution with `tdd_tests_not_executed`.

### test_runtime_evidence_accepts_when_all_tdd_named_tests_execute

Maps to: P2
RED: full TDD coverage falsely fails.
GREEN: all resolved TDD names appear in executed pytest targets.

### test_runtime_evidence_fails_when_tdd_test_name_is_unresolved

Maps to: P2
RED: typoed TDD test names silently pass.
GREEN: emit red `tdd_test_names_unresolved`.

### test_axi_trends_surfaces_by_era_in_json_and_toon

Maps to: P3
RED: by-era fields expose incident share as `rate`, making D1 look decided without a run denominator.
GREEN: JSON exposes incident counts, run counts by era, incidents-per-run rates, and share fields; TOON-lite exposes the incident and run-count era fields.

### test_axi_trends_uses_legacy_incident_eras_as_denominators

Maps to: P3
RED: legacy trend rows without `run_era` put every denominator in the computed-at era, making MCP incident rates read as zero or undefined.
GREEN: when legacy rows contain incident-era details, use those eras as denominator buckets before falling back to computed_at.

### test_transport_incident_events_are_written_from_public_axi_and_mcp_poll_failure_boundaries

Maps to: P4
RED: missing-job poll incident tests use only internal helpers.
GREEN: AXI and MCP public polling write `poll_failure` incidents.

### test_read_gate_transcript_includes_skill_receipt_validation

Maps to: P2
RED: an existing workflow transcript test writes source TDD/implementation fixtures that no longer match its runtime tests, causing the new floor to block before skill receipt transcript validation.
GREEN: the fixture declares and executes matching runtime tests so the skill receipt validation path remains covered under the stronger floor.

### test_workflow_round_objection_preserves_runtime_probe_details

Maps to: P2
RED: after a runtime-native TDD coverage failure, the next corrective context collapses to generic `agents have not both accepted yet`, hiding the missing test nodeids.
GREEN: workflow round objections preserve red runtime probe details, including `tdd_tests_not_executed` and the missing TDD nodeids, so the next lead attempt receives actionable corrective evidence.

### test_execution_gate_instruction_includes_tdd_runtime_contract

Maps to: P2
RED: execution/outcome lead prompts omit the TDD test contract, leaving the lead to guess which tests must be listed in `outcome.tests`.
GREEN: execution/outcome instructions include a runtime TDD test contract that names every extracted TDD test, states that missing runtime-native coverage blocks the gate, and forbids non-canonical `accept_with_residual` in favor of exact commands for the supervisor runtime floor to rerun.

### test_workflow_tdd_test_names_reads_tdd_artifacts

Maps to: P2
RED: workflow instruction construction cannot derive TDD test names from generated source artifacts.
GREEN: the workflow instruction helper reads `tdd_plan` artifacts and returns the ordered TDD test names used by the runtime contract block.

### test_postgres_trends_details_and_incident_aggregation_match_sqlite

Maps to: P5
RED: Postgres detail JSON and incident aggregation are untested.
GREEN: Postgres trend rows roundtrip details and aggregate incidents from `read_events_since`.

### test_axi_toon_poll_records_format_metric

Maps to: P6
RED: default poll output emits no format metric.
GREEN: default TOON-lite poll writes `supervisor_axi_format_metric`.

### test_lesson_injection_says_lessons_are_not_standalone_gate_decisions

Maps to: P7
RED: the injected lesson block says lessons are advisory but leaves room for a prior FM-1.3 lesson to become a standalone blocker.
GREEN: the block explicitly says not to block/revise/deny/accept solely because a lesson exists and requires current evidence of the same handoff, artifacts, and source state before applying step-repetition.

## RED/GREEN Plan

RED: Add the baseline tests before relying on the existing dirty marker patch.
GREEN: Keep marker writes best-effort but make fallback reasons receipt-visible.

RED: Add TDD missing and unresolved tests against `collect_runtime_evidence`.
GREEN: Add a runtime-native `runtime_tdd_test_coverage` receipt.

RED: Add AXI and Postgres public-boundary assertions for hidden metrics.
GREEN: Enrich trend rows and CLI output without write side effects.

RED: Add a lesson-injection assertion proving advisory lessons are not standalone gate decisions.
GREEN: Strengthen the lesson block while keeping lesson events observational and non-authoritative.
