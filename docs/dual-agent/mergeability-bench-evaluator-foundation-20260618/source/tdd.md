# TDD Plan

## MB-1 Tests: Schema And Loader

test_load_mergeability_tasks_reads_typed_fixture_contract

Maps to: Slice 1, P1.

RED: importing `supervisor.mergeability_bench` or calling `load_mergeability_tasks` fails before implementation.

GREEN: the loader reads the local fixture corpus and returns a task with fixture ref, allowed paths, hidden commands, reverse commands, blocker criteria, and weighted secondary rubric fields.

Additional tests:
- test_invalid_mergeability_task_fails_with_actionable_error
- test_mergeability_result_serializes_replayable_hashes

## MB-2 Tests: Deterministic Isolated Grading

test_mergeability_controls_discriminate_noop_known_bad_and_known_good

Maps to: Slice 2, P1, P2, P4.

RED: no grader exists, so the three controls cannot be evaluated.

GREEN: no-op and known-bad candidates fail deterministic blockers with score zero, while known-good passes with score one.

Additional tests:
- test_mergeability_candidate_hidden_material_edit_is_rejected
- test_mergeability_candidate_mutable_path_escape_is_rejected
- test_reverse_classical_requires_candidate_tests_fail_on_base

test_reverse_classical_rejects_candidate_without_submitted_tests

Maps to: Slice 2, P2.

RED: a candidate can repair hidden behavior without submitting a regression test, and the reverse command's missing file error is counted as a successful fail-on-base proof.

GREEN: a candidate with no submitted test files fails reverse-classical status and receives final score zero, while candidates with real tests continue through the reverse command.

## MB-3 Tests: AutoResearch Evaluator Integration

test_autoresearch_mergeability_evaluator_emits_computed_runtime_native_metric

Maps to: Slice 3, P3, P4.

RED: the evaluator script does not exist or emits no mergeability runtime evidence.

GREEN: the evaluator script runs against the fixture candidate, computes a metric from grading, emits runtime-native receipt metadata, and includes evidence refs.

Additional tests:
- test_autoresearch_mergeability_evaluator_works_with_live_trials
- test_autoresearch_saturated_zero_variance_replay_stays_non_applyable
