# Issues: Agentic Eval Acceleration

Task id: `agentic-eval-acceleration-20260603`

## Slice 1: Add Acceleration Ratios And Percentiles

Priority: P1

Scope: Extend `supervisor/agentic_eval.py` so
`agentic_eval_runner(dataset_path=...)` computes task-relative
`acceleration_ratio` for each row against the same task's `lead_direct` arm and
adds per-mode p50/p95 acceleration ratios to the report summary.

PRD promises: P1.

Acceptance Criteria:

- [ ] Every row has `acceleration_ratio` or
  `acceleration_ratio_unavailable_reason`.
- [ ] `lead_direct` rows report a baseline ratio of `1.0` when timing is
  available.
- [ ] `agentic_allowed` and `agentic_required` summary buckets expose
  deterministic p50 and p95 acceleration ratios.

## Slice 2: Add Quality-Gated Qualification

Priority: P1

Scope: Add row-level and per-mode qualification semantics to the report. A
fan-out row qualifies only when the workflow is accepted, score is at least the
lead score, missed issues and rejected gates are no worse than lead, and
acceleration is at least `1.2`.

PRD promises: P2.

Acceptance Criteria:

- [ ] A faster but `blocked` arm has `qualifies=false`.
- [ ] Lower score, higher missed issues, higher rejected gates, or insufficient
  acceleration each produce a named failing predicate.
- [ ] A high-quality accepted fan-out row with ratio `>= 1.2` qualifies.

## Slice 3: Add Latency And Overhead Fields

Priority: P1

Scope: Carry `time_to_first_useful_finding`, `time_to_accepted_outcome`,
`orchestration_overhead_s`, `reviewer_time_s`, and `worker_idle_wait_s` from
arm metrics into rows and summaries. Missing values must be `null` with a
paired unavailable reason.

PRD promises: P3.

Acceptance Criteria:

- [ ] Present timing metrics are copied as numeric row values.
- [ ] Missing timing metrics are exported as `null` with `not_recorded`.
- [ ] Summary latency buckets include avg, p50, p95, and unavailable counts.

## Slice 4: Add Accepted Parallelism Replay Corpus

Priority: P2

Scope: Extend `tests/fixtures/agentic_eval/three_arm_tasks.yaml` with at least
two parallelism-friendly accepted cases and export a deterministic report plus
cassettes under this task's artifact folder.

PRD promises: P4.

Acceptance Criteria:

- [ ] The corpus contains accepted all-mode cases for multi-file code
  archaeology and affected-path dependency tracing.
- [ ] The replay report has a stable `report_sha256`.
- [ ] Exported cassettes, dataset, rows, evidence, and manifest are committed.

## Slice 5: Preserve Report-Only Policy

Priority: P1

Scope: Add a recommendation-only payload that summarizes qualifying fan-out
modes without mutating config, policy defaults, worker caps, or production
reviewer behavior.

PRD promises: P5.

Acceptance Criteria:

- [ ] `default_change_allowed` remains `false`.
- [ ] `agentic_lead_policy_snapshot.policy` remains `off`.
- [ ] `recommendation.report_only=true` and `recommendation.policy_mutated=false`.

## Coverage

- P1: Slice 1.
- P2: Slice 2.
- P3: Slice 3.
- P4: Slice 4.
- P5: Slice 5.
