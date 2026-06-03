# Implementation Plan: Agentic Eval Trials And Strata

Task id: `agentic-eval-trials-strata-20260603`

## Files / Modules To Touch

- `supervisor/agentic_eval.py`
- `tests/test_agentic_eval.py`
- `tests/fixtures/agentic_eval/three_arm_tasks.yaml`
- `docs/dual-agent/agentic-eval-trials-strata-20260603/test-evidence.md`

## Plan

1. Add RED tests for repeated-trial median reduction, quality instability,
   class-specific summaries, and N-gated p95.
2. Extend task normalization with `task_class`, defaulting existing fixtures to
   a stable class when absent.
3. Add arm trial normalization while preserving existing single-arm fixture
   shape.
4. Build per-trial rows internally, reduce to one median row per task/mode, then
   compute acceleration and qualification from reduced rows.
5. Add `summary_by_task_class` and have recommendations read class/mode buckets.
6. Keep legacy `summary` for compatibility, but mark it as informational and do
   not use it for class-level recommendation decisions.
7. Run focused tests, bridge/corpus tests, hygiene, and the full suite.

## Risks

- Existing stable report hashes will change because rows gain fields and the
  summary gains task-class buckets.
- Backward compatibility requires old fixtures without `task_class` or trials
  to remain valid.
- P95 gating must count tasks in the class/mode bucket, not repeated trials.

## Traceability

- P1 maps to `test_agentic_eval_runner_reduces_repeated_trials_to_median_wall_clock`.
- P2 maps to `test_agentic_eval_runner_flags_quality_unstable_across_trials`.
- P3 maps to `test_agentic_eval_report_segments_win_gate_by_task_class`.
- P4 maps to `test_agentic_eval_report_suppresses_p95_until_class_has_twenty_tasks`
  and `test_agentic_eval_report_emits_p95_when_class_has_twenty_tasks`.
- P5 maps to `test_agentic_eval_runner_is_report_only`.

## TDD Test References

- `test_agentic_eval_runner_reduces_repeated_trials_to_median_wall_clock`
- `test_agentic_eval_runner_flags_quality_unstable_across_trials`
- `test_agentic_eval_report_segments_win_gate_by_task_class`
- `test_agentic_eval_report_suppresses_p95_until_class_has_twenty_tasks`
- `test_agentic_eval_report_emits_p95_when_class_has_twenty_tasks`
- `test_agentic_eval_runner_is_report_only`
