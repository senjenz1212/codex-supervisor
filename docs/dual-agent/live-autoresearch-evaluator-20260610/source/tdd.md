# TDD Plan: Live AutoResearch Evaluators

## RED/GREEN Plan

### test_autoresearch_validation_rejects_fixture_metrics_without_evaluator_execution

Maps to: P2, P4, Slice 2

RED: current validation accepts hand-entered `metric_trials`.

GREEN: validation rejects metrics unless `metric_source`, evaluator-run ref, and evaluator-run hash prove evaluator execution.

### test_autoresearch_live_evaluator_runs_k_trials_and_records_iqr

Maps to: P1, P2, Slice 1

RED: live mode does not execute an evaluator or compute trials.

GREEN: `run_autoresearch_fixture(...)` runs the evaluator three times, records computed metrics, and reports median plus IQR.

### test_autoresearch_live_evaluator_hash_mismatch_blocks_execution

Maps to: P1, Slice 1

RED: a changed evaluator is not checked before live execution.

GREEN: hash mismatch rejects the attempt and the evaluator marker file is absent.

### test_autoresearch_live_evaluator_blocks_mutable_path_escape

Maps to: P3, Slice 3

RED: no isolated worktree or side-effect audit exists.

GREEN: writes outside mutable paths become validation errors and the source checkout is untouched.

### test_autoresearch_validation_flags_dangling_evidence_ref

Maps to: P4, Slice 2

RED: validation only checks that evidence refs are present.

GREEN: artifact and evaluator-run refs must resolve to declared runtime evidence.

### test_autoresearch_validation_flags_zero_variance_trials

Maps to: P4, Slice 2

RED: identical trial metrics are not surfaced.

GREEN: repeated identical values add `zero_variance_trials` while preserving acceptance when no fatal error exists.

### test_autoresearch_cli_allow_live_executes_evaluator

Maps to: P1, P2, P5, Slice 4

RED: `--allow-live` only suppresses a guard and still replays fixture metrics.

GREEN: live CLI output contains evaluator execution provenance and a run artifact.

### test_autoresearch_report_only_invariants_remain_false_for_live_run

Maps to: P5, Slice 4

RED: no live report proves the invariant.

GREEN: live report records `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.
