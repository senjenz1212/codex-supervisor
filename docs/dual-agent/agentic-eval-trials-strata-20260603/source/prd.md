# PRD: Agentic Eval Repeated Trials And Task-Class Strata

Task id: `agentic-eval-trials-strata-20260603`

## Problem Statement

The agentic lead eval reports acceleration and quality-gated wins, but it still
aggregates all tasks together and treats one replay as decisive. That can make a
noisy run look like a win, can hide task-class-specific regressions, and can
emit p95 values from tiny samples that do not support tail-latency claims.

## Solution

Add repeated-trial support and reduce each task/mode to median wall-clock timing
before computing acceleration or the win gate. Carry `task_class` from the
dataset into each report row, summarize by `task_class` and mode, and suppress
class-level p95 acceleration until at least twenty tasks exist in that class.
The eval remains report-only and never changes agentic policy.

## User Stories

- As an operator, I can compare fan-out speed by task class instead of trusting a
  pooled verdict across unrelated work.
- As a reviewer, I can see when repeated trials disagree on quality signals
  instead of silently averaging failures away.
- As a release owner, I can reject p95 claims from small samples while still
  inspecting p50, IQR, min, and max.
- As a maintainer, I can keep using deterministic fixture replay without live
  model calls or config mutation.

## PRD Promise Contracts

P1. Repeated Trials Use Median Timing

- Public boundary: `supervisor.agentic_eval.agentic_eval_runner` and report
  rows.
- Allowed outcomes: dataset arms can provide repeated trials; the runner emits
  one task/mode row using median `wall_clock_s`, and acceleration/qualification
  compare median timings.
- Forbidden outcomes: averaging wall-clock by default, computing acceleration
  from a non-median trial, or changing quality by averaging trial scores.

P2. Quality Instability Is Explicit

- Public boundary: report rows for task/mode results.
- Allowed outcomes: if repeated trials for one task/mode vary in `score` or
  `missed_issues`, the row sets `quality_unstable_across_trials=true` and names
  the unstable fields.
- Forbidden outcomes: silently averaging score or missed issues across trials,
  or letting an unstable row appear indistinguishable from stable evidence.

P3. Task-Class Summaries Are The Decision Surface

- Public boundary: report summary and recommendation.
- Allowed outcomes: tasks carry `task_class`, rows preserve it, and summaries are
  segmented by `task_class` and mode with independent qualify/fail counts.
- Forbidden outcomes: pooling verdicts across task classes when deciding whether
  a mode qualifies.

P4. P95 Is N-Gated

- Public boundary: class/mode summary buckets.
- Allowed outcomes: class buckets report p50, IQR, min, and max for acceleration;
  p95 is present only when the class has at least twenty tasks, otherwise it is
  `null` with reason `insufficient_n_for_p95`.
- Forbidden outcomes: emitting p95 from fewer than twenty tasks or hiding the
  reason a p95 is unavailable.

P5. Report-Only Invariant

- Public boundary: report metadata and config state.
- Allowed outcomes: `default_change_allowed=false`, the policy snapshot remains
  `off`, and the report only recommends operator review.
- Forbidden outcomes: changing `agentic_lead_policy`, mutating config defaults,
  or touching scaling/state infrastructure.

## Implementation Decisions

- Extend the existing `agentic-lead-eval-dataset/v1` loader rather than adding a
  new corpus format.
- Support trial lists on individual arms; existing single-arm fixtures normalize
  to a single trial for backward compatibility.
- Preserve existing row fields for compatibility, but make class summaries and
  class-scoped recommendation the authoritative decision surface.
- Keep p95 gating at the task-count level specified by the requirement.

## Testing Decisions

- First RED tests call `agentic_eval_runner` or `build_agentic_eval_report`.
- Cover trial median reduction, quality instability, class-specific win/loss
  independence, p95 suppression at N<20, and p95 presence at N>=20.
- Retain report-only and no-live-call guards.

## Out Of Scope

This slice does not build a larger corpus or record new live workflow arms; it
only changes how the existing eval runner interprets replayed task/mode data.
It also does not enable fan-out, change `agentic_lead_policy`, mutate config
defaults, alter worker caps, touch `supervisor/state.py`, or change transport
behavior. Those decisions remain separate supervised slices because this work is
measurement-only and report-only.
