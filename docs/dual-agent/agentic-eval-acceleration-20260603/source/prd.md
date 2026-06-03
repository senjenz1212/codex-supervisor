# PRD: Agentic Eval Acceleration Metrics With Quality Guardrails

Task id: `agentic-eval-acceleration-20260603`

## Problem Statement

The agentic lead eval now derives quality signals from verdict evidence, but it
still does not answer the operator's central question: whether fan-out
accelerates accepted work at equal budget without hiding quality regressions.
The committed bridge sample is also misleading for acceleration because every
arm ended `blocked`; any speed ratio from that data measures orchestration
overhead, not useful parallel work.

## Solution

Make speed the headline metric while preserving report-only guardrails. The
runner must compute per-row acceleration ratios against the `lead_direct` arm,
aggregate p50/p95 ratios per mode, and mark whether each fan-out mode qualifies
as a win only when it is faster and at least as good on evidence-derived
quality. Latency and overhead fields must be first-class and explicitly nullable
with reasons when the replay cannot provide them. The corpus must include
parallelism-friendly accepted replay cases so the report can distinguish useful
fan-out from blocked-run overhead.

## User Stories

- As an operator, I can see whether `agentic_allowed` or `agentic_required`
  actually accelerates accepted outcomes at equal budget.
- As a reviewer, I can reject a faster run as a "win" when it is blocked,
  lower-scoring, misses more issues, or rejects more gates than `lead_direct`.
- As a maintainer, I can inspect latency and overhead components without the
  runner fabricating unavailable timing data.
- As a release owner, I can read a recommendation without the eval flipping
  `agentic_lead_policy` or changing defaults.

## PRD Promise Contracts

P1. Acceleration Is A Headline Metric

- Public boundary: `supervisor.agentic_eval.agentic_eval_runner` and exported
  report JSON.
- Allowed outcomes: each row has `acceleration_ratio` relative to the same
  task's `lead_direct` wall clock; the summary reports p50 and p95 ratios per
  mode.
- Forbidden outcomes: comparing across different tasks, omitting p50/p95, or
  treating missing wall-clock data as a speed win.

P2. Win Conditions Are Quality-Gated

- Public boundary: row-level `qualifies` and
  `qualification_failing_predicates`.
- Allowed outcomes: a fan-out mode qualifies only when it is `accepted`, has a
  score at least as high as `lead_direct`, has no more missed issues, has no more
  rejected gates, and reaches `acceleration_ratio >= 1.2`.
- Forbidden outcomes: a faster `blocked` arm qualifies; self-reported quality
  metrics override evidence-derived quality; failure reasons are hidden.

P3. Latency And Overhead Are Explicit

- Public boundary: row timing fields in report rows and exported JSON.
- Allowed outcomes: rows include `time_to_first_useful_finding`,
  `time_to_accepted_outcome`, `orchestration_overhead_s`, `reviewer_time_s`, and
  `worker_idle_wait_s`, each either numeric or `null` with a paired
  unavailable reason.
- Forbidden outcomes: inventing timing values when replay evidence lacks them.

P4. Parallelism Corpus Completes

- Public boundary: deterministic fixture replay dataset and report artifacts
  under this task folder.
- Allowed outcomes: at least two parallelism-friendly cases reach `accepted` in
  all three modes and replay to a stable report hash.
- Forbidden outcomes: drawing acceleration conclusions from the all-blocked
  bridge sample.

P5. Report-Only Invariant

- Public boundary: report metadata and config state.
- Allowed outcomes: the report emits a recommendation only;
  `default_change_allowed` remains `false`, policy snapshot remains `off`, and
  no config or worker limit is mutated.
- Forbidden outcomes: enabling fan-out, changing `agentic_lead_policy`, touching
  `AGENTIC_WORKER_MAX_SUBAGENTS`, or changing `supervisor/state.py`.

## Implementation Decisions

- Keep the dataset schema and runner boundary stable; enrich the row and summary
  payloads without changing policy defaults.
- Compute acceleration after all rows are built so each task can use the
  authoritative `lead_direct` row for comparison.
- Use evidence-derived `score`, `missed_issues`, and `rejected_gates` from the
  prior slice for the quality predicates.
- Add a report-level recommendation that is explicitly advisory and is computed
  from qualifying task counts, not from self-reported arm claims.

## Testing Decisions

- First RED tests call `agentic_eval_runner`; helper tests are secondary.
- Cover acceleration p50/p95 math, each win-condition predicate, blocked-faster
  non-qualification, nullable latency fields, report-only invariants, and stable
  replay hash for the parallelism-friendly corpus.

## Out Of Scope

- Flipping `agentic_lead_policy` or enabling fan-out.
- Changing worker count limits or production reviewer-panel behavior.
- Running live model/provider calls in tests.
- `supervisor/state.py`, scaling, or transport changes.
