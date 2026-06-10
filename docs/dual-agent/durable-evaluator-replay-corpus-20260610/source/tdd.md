# TDD Plan

## test_autoresearch_live_evaluator_executes_through_durable_job_row

Maps to: Slice 1, P1, P5

RED: Call `run_autoresearch_fixture(..., execution_mode="live")` and assert that no durable job row exists under the current direct-call implementation.

GREEN: Add the durable evaluator job adapter so the test observes one terminal job row, an `autoresearch_evaluator` request payload, a stable idempotency token, and an accepted report-only validation record.

## test_autoresearch_durable_evaluator_resumes_after_midrun_crash

Maps to: Slice 2, P2

RED: Use a crash-once evaluator that exits after trial one and prove the current runner either loses the progress or reruns trial zero.

GREEN: Persist per-trial progress after each successful evaluator trial and reload that progress on retry so trial zero executes once while the final run reports all k trials.

## test_autoresearch_live_evaluator_budget_overrun_is_flagged_and_rejected

Maps to: Slice 2, P3

RED: Use an evaluator that reports cumulative `cost_usd` above `budget_usd` and observe that the attempt can validate without a budget flag.

GREEN: Stop after the budget overrun is observed, add `budget_exceeded` to execution errors, map that to gaming flags, and reject the attempt.

## test_autoresearch_live_evaluator_timeout_is_flagged_and_rejected

Maps to: Slice 2, P3

RED: Use an evaluator that sleeps past `timeout_s` and observe that validation lacks a precise timeout flag.

GREEN: convert subprocess timeout failures into `timeout` execution errors and reject the attempt with a machine-readable flag.

## test_autoresearch_live_evaluator_partial_progress_timeout_is_terminal

Maps to: Slice 2, P3

RED: Complete trial zero, time out on trial one, retry the same run, and observe that the current implementation can requeue the timeout forever because partial metrics make the timeout look like a retryable crash.

GREEN: Treat timeout as a terminal limit failure even when earlier trials completed, persist the failed terminal job outcome, replay it on retry, and prove no completed trial is rerun.

## test_autoresearch_default_replay_corpus_evaluator_produces_pass_rate

Maps to: Slice 3, P4

RED: Submit a live experiment with empty evaluator ref/hash and observe that no executable metric source is available.

GREEN: Resolve the shipped replay-corpus evaluator, hash-pin it, execute k trials, and assert pass-rate metrics plus median/IQR are computed from evaluator output.

## test_autoresearch_report_only_invariants_remain_false_for_live_run

Maps to: Slice 4, P5

RED: Guard against any AutoResearch result setting `default_change_allowed`, `policy_mutated`, or `gate_advanced`.

GREEN: Keep the existing report-only validation fields false for successful, failed, default-evaluator, and resumed evaluator runs.

## Regression Command Plan

Maps to: Slice 4, P1, P2, P3, P4, P5

RED: Run focused tests before implementation and observe failures in durable dispatch, resume, limit flags, and default evaluator behavior.

GREEN: Run `.venv/bin/python -m pytest tests/test_autoresearch.py tests/test_autoresearch_policy_evolution.py tests/test_agentic_eval_corpus.py tests/test_replay_cli.py -q`, then run `.venv/bin/python -m pytest -q`.
