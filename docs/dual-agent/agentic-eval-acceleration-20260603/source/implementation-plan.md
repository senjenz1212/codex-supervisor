# Implementation Plan: Agentic Eval Acceleration

Task id: `agentic-eval-acceleration-20260603`

## Files / Modules To Touch

- `supervisor/agentic_eval.py`: add row enrichment for acceleration ratios,
  quality-gated qualification, latency/overhead fields, per-mode summaries, and
  report-only recommendations.
- `supervisor/agentic_eval_assembler.py`: preserve timing metrics from recorded
  workflow results when assembling replay datasets.
- `tests/test_agentic_eval.py`: add public-boundary tests for acceleration,
  quality predicates, blocked fast arms, latency null reasons, replay hash, and
  report-only invariants.
- `tests/fixtures/agentic_eval/three_arm_tasks.yaml`: add timing metrics and
  two accepted parallelism-friendly replay cases.
- `docs/dual-agent/agentic-eval-acceleration-20260603/agentic-eval-parallelism/`:
  export the deterministic dataset, cassettes, report, evidence, rows, manifest,
  and comparison summary.

## Steps

1. Extend `build_agentic_eval_report` so it enriches normalized rows after all
   task arms are known, using each task's `lead_direct` row as the comparison
   baseline.
2. Compute `acceleration_ratio` and unavailable reasons, then apply the quality
   gate predicates from P2 without replacing evidence-derived quality signals.
3. Carry latency metrics from arm metrics into rows, summarize them per mode,
   and represent missing data as `null` plus `not_recorded`.
4. Add a recommendation payload that reports qualifying fan-out modes while
   keeping `default_change_allowed=false` and policy mutation flags false.
5. Extend the fixture corpus with the two accepted parallelism cases and export
   replay artifacts for operator review.
6. Run focused, adjacent, compile/diff, and full-suite validation.

## Risks

- A fast but blocked arm could be mislabeled as a win if the status predicate is
  omitted; the blocked-faster regression test guards this.
- Missing timing data could be confused with zero overhead; the nullable reason
  fields avoid fabricated timing values.
- The recommendation output could be mistaken for a policy flip; report-only
  metadata and tests pin `agentic_lead_policy` to `off`.
- Adding fields to summary rows could perturb deterministic report hashes; the
  stable replay-hash test makes drift explicit.

## Traceability

- P1 / Issue 1 maps to
  `test_agentic_eval_runner_reports_acceleration_percentiles` and the
  `acceleration_ratio_p50` / `acceleration_ratio_p95` summary fields.
- P2 / Issue 2 maps to
  `test_agentic_eval_quality_gated_win_condition_truth_table` and
  `test_agentic_eval_blocked_faster_arm_never_qualifies`.
- P3 / Issue 3 maps to
  `test_agentic_eval_latency_fields_are_values_or_unavailable_reasons`.
- P4 / Issue 4 maps to
  `test_agentic_eval_parallelism_corpus_replays_to_stable_report_sha256` and
  the exported replay artifacts.
- P5 / Issue 5 maps to `test_agentic_eval_runner_is_report_only`.
