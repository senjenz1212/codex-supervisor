# TDD Plan

## MBP-1 Corpus Manifest And Calibration

test_mergeability_corpus_manifest_requires_positive_and_negative_controls

Maps to: Slice 1, P1, P2.

Public boundary: `build_mergeability_corpus_manifest` and `validate_mergeability_corpus` loading real fixture files from `tests/fixtures/mergeability_bench/`.

RED: the current bench has no manifest or calibration public boundary, so there is no way to reject a task that lacks both positive and negative controls.

GREEN: the manifest records task hashes, candidate hashes, control roles, expected pass/fail outcomes, and calibration eligibility. Strict calibration raises when a task lacks positive or negative controls.

Additional tests:
test_mergeability_calibration_rejects_broken_known_good_control

test_mergeability_calibration_covers_seeded_failure_modes

test_saturated_all_one_results_are_non_applyable

## MBP-2 Paired Acceptance Pilot

test_paired_acceptance_pilot_reports_baseline_false_accept_and_supervisor_rejection

Maps to: Slice 2, P3, P4.

Public boundary: `run_paired_acceptance_pilot` over the manifest-backed fixed candidate pool.

RED: no paired harness exists, and the existing evaluator returns only one candidate metric at a time.

GREEN: baseline and Supervisor arms evaluate the same candidate ids. Baseline accepts at least one seeded bad candidate from self-reported or visible-test evidence, while Supervisor rejects the same candidate because mergeability blockers fail.

Additional tests:
test_paired_acceptance_pilot_computes_true_accept_and_false_reject_rates

test_paired_acceptance_pilot_uses_identical_candidate_pool_for_both_arms

## MBP-3 Artifact Export And Report-Only Invariants

test_paired_acceptance_pilot_exports_replayable_artifacts

Maps to: Slice 3, P1, P3, P4.

Public boundary: `run_paired_acceptance_pilot(..., output_dir=...)` writing evidence artifacts.

RED: no corpus manifest, calibration summary, paired report, or per-task JSONL artifact is exported.

GREEN: the output directory contains `corpus_manifest.json`, `calibration_summary.json`, `paired_acceptance_report.json`, and `per_task_results.jsonl`. Every row includes the mergeability receipt id or receipt payload, and the paired report keeps `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

Additional tests:
test_existing_mergeability_evaluator_quality_checks_remain_green

test_paired_acceptance_report_cannot_create_applyable_policy_claim
