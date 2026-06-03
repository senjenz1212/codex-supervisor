# TDD Plan: Agentic Eval Acceleration

Task id: `agentic-eval-acceleration-20260603`

## Public Boundary RED/GREEN Tests

### test_agentic_eval_runner_reports_acceleration_percentiles

Maps to: Issue 1, P1.

RED: `agentic_eval_runner(dataset_path=FIXTURE)` returns rows without
`acceleration_ratio`, and report summaries do not expose
`acceleration_ratio_p50` or `acceleration_ratio_p95`.

GREEN: each row reports a task-relative ratio against `lead_direct`; summary
values are deterministic for `lead_direct`, `agentic_allowed`, and
`agentic_required`.

### test_agentic_eval_quality_gated_win_condition_truth_table

Maps to: Issue 2, P2.

RED: `build_agentic_eval_report([...])` has no `qualifies` field and cannot
name why a fan-out arm fails the quality gate.

GREEN: blocked status, lower score, higher missed issues, higher rejected
gates, and insufficient acceleration each independently produce
`qualifies=false` with a named failing predicate.

### test_agentic_eval_blocked_faster_arm_never_qualifies

Maps to: Issue 2, P2.

RED: a replay arm with `wall_clock_s=10` and `workflow_status=blocked` can still
look like a speed win.

GREEN: the row retains its high `acceleration_ratio`, but `qualifies=false` and
`workflow_status_not_accepted` is present.

### test_agentic_eval_latency_fields_are_values_or_unavailable_reasons

Maps to: Issue 3, P3.

RED: latency and overhead metrics are absent from exported rows, so overhead
cannot be inspected.

GREEN: present metrics are numeric row values, and missing metrics are `None`
with `<field>_unavailable_reason == "not_recorded"`.

### test_agentic_eval_parallelism_corpus_replays_to_stable_report_sha256

Maps to: Issue 4, P4.

RED: the fixture corpus lacks accepted parallelism-friendly cases and has no
stable acceleration report hash.

GREEN: the corpus contains `multi-file-code-archaeology` and
`affected-path-dependency-trace`, all three modes are accepted, and the report
hash is stable.

### test_agentic_eval_runner_is_report_only

Maps to: Issue 5, P5.

RED: recommendation output is missing or policy metadata can be interpreted as
mutated.

GREEN: `recommendation.report_only=true`, `recommendation.policy_mutated=false`,
`default_change_allowed=false`, and policy snapshot remains `off`.

## Helper Coverage

- `_percentile` uses deterministic interpolation for small samples.
- `_latency_fields_from_metrics` differentiates missing and invalid timing
  fields.
- `_qualification_result` keeps evidence-derived quality authoritative.

## Regression Guardrails

- `missed_issues` and `rejected_gates` remain evidence/workflow-derived.
- `wall_clock_s` and `cost_usd` remain metrics-sourced.
- Fixture replay never calls live workflow runners.
- No changes to `agentic_lead_policy`, worker limits, or `supervisor/state.py`.

## Commands

- `uv run pytest tests/test_agentic_eval.py -q`
- `uv run pytest tests/test_agentic_eval.py tests/test_agentic_eval_bridge.py tests/test_agentic_eval_corpus.py -q`
- `uv run --extra dev pytest -q`
