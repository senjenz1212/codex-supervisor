# TDD Plan: Agentic Eval Trials And Strata

Task id: `agentic-eval-trials-strata-20260603`

## Public Boundary RED/GREEN Tests

### test_agentic_eval_runner_reduces_repeated_trials_to_median_wall_clock

Maps to: Slice 1, P1.

RED: a dataset arm with repeated trials either fails to load or reports the
first trial's wall clock.

GREEN: `agentic_eval_runner(dataset_path=...)` emits one task/mode row whose
`wall_clock_s` is the median trial value, and acceleration uses that median.

### test_agentic_eval_runner_flags_quality_unstable_across_trials

Maps to: Slice 1, P2.

RED: repeated trials with different evidence-derived scores or missed-issue
counts silently collapse into one row with no warning.

GREEN: the reduced row sets `quality_unstable_across_trials=true` and includes
the unstable field names.

### test_agentic_eval_report_segments_win_gate_by_task_class

Maps to: Slice 2, P3.

RED: `build_agentic_eval_report([...])` pools all tasks by mode so one winning
class can mask another losing class.

GREEN: `summary_by_task_class` reports independent buckets where one class can
qualify and another can fail for the same mode.

### test_agentic_eval_report_suppresses_p95_until_class_has_twenty_tasks

Maps to: Slice 3, P4.

RED: a class with fewer than twenty tasks still reports
`acceleration_ratio_p95`.

GREEN: the bucket emits `acceleration_ratio_p95=null` with reason
`insufficient_n_for_p95`, while preserving p50, IQR, min, and max.

### test_agentic_eval_report_emits_p95_when_class_has_twenty_tasks

Maps to: Slice 3, P4.

RED: the N gate suppresses p95 even when a class has twenty task rows.

GREEN: the class/mode bucket emits deterministic `acceleration_ratio_p95`.

### test_agentic_eval_runner_is_report_only

Maps to: Slice 4, P5.

RED: trial or class support mutates policy metadata or permits live calls.

GREEN: `default_change_allowed=false`, `report_only.policy_mutated=false`, and
the policy snapshot remains `off`.

## Helper Coverage

- `_median` handles odd and even trial counts deterministically.
- `_quartiles` produces deterministic IQR values for class summaries.
- `_p95_with_sample_gate` reports a reason when N is too small.

## Regression Guardrails

- `missed_issues` and `rejected_gates` remain evidence/workflow-derived.
- `wall_clock_s` and `cost_usd` remain metrics-sourced.
- Fixture replay never calls live workflow runners.
- No changes to `agentic_lead_policy`, worker limits, or `supervisor/state.py`.

## Commands

- `uv run pytest tests/test_agentic_eval.py -q`
- `uv run pytest tests/test_agentic_eval.py tests/test_agentic_eval_bridge.py tests/test_agentic_eval_corpus.py -q`
- `uv run --extra dev pytest -q`
