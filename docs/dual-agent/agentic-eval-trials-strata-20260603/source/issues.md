# Issues: Agentic Eval Trials And Strata

Task id: `agentic-eval-trials-strata-20260603`

## Slice 1: Repeated-Trial Median Rows

Priority: P1

Scope: Extend the dataset runner so a task arm can contain repeated trial
records while existing single-record arms still load unchanged. Emit one
task/mode row with median `wall_clock_s`, trial count, trial wall-clock values,
and explicit quality instability metadata.

Acceptance Criteria:

- [ ] `agentic_eval_runner` accepts arm-level repeated trials.
- [ ] `wall_clock_s` on the report row is the median trial wall clock.
- [ ] `acceleration_ratio` and `qualifies` compute from median rows.
- [ ] Score or missed-issue variation sets
  `quality_unstable_across_trials=true`.

PRD promise: P1, P2

## Slice 2: Task-Class Segmented Summary

Priority: P1

Scope: Carry `task_class` into normalized tasks and rows, then summarize by
`task_class` and mode. Keep compatibility with existing fixtures through a
default class.

Acceptance Criteria:

- [ ] Rows include `task_class`.
- [ ] The summary has independent class/mode buckets.
- [ ] A class where fan-out wins and a class where it loses produce independent
  `qualifies` values.
- [ ] Recommendation reads the class summaries instead of a pooled verdict.

PRD promise: P3

## Slice 3: N-Gated P95

Priority: P2

Scope: Add acceleration distribution fields per class/mode: p50, IQR, min,
max, and p95 only when there are at least twenty tasks in the class bucket.

Acceptance Criteria:

- [ ] Buckets with fewer than twenty tasks set `acceleration_ratio_p95=null`.
- [ ] Suppressed p95 buckets include reason `insufficient_n_for_p95`.
- [ ] Buckets with at least twenty tasks emit deterministic p95.
- [ ] p50, IQR, min, and max remain available for smaller buckets.

PRD promise: P4

## Slice 4: Report-Only Regression Guard

Priority: P1

Scope: Preserve the existing report-only invariant and no-live-call replay
behavior while adding trials and task-class summaries.

Acceptance Criteria:

- [ ] `default_change_allowed` stays `false`.
- [ ] `agentic_lead_policy_snapshot.policy` stays `off`.
- [ ] Fixture replay does not invoke live workflow runners.
- [ ] No config, state, or scaling defaults are mutated.

PRD promise: P5
