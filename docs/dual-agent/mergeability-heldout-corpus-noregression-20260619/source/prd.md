## Problem Statement

The mergeability bench now separates public checking from the full reviewer-panel gate, but the held-out corpus still needs stronger calibration guarantees before any auto-improvement number can be trusted. Operators need a larger oracle-isolated fixture set with explicit split metadata, per-task controls, no-regression behavior that protects real solved cases, and confidence intervals that make the uncertainty visible instead of letting fixture-scale peaks look like policy evidence.

## Solution

Extend the mergeability corpus and paired acceptance report through the existing `run_paired_acceptance_pilot` and `validate_mergeability_corpus` interfaces. The corpus must contain multiple held-out tasks with public prompts, mutable-path policy, protected hidden oracle paths, positive controls, negative controls, and public-pass hidden-fail traps. The report must keep held-out metrics separate from dev or in-sample metrics, publish denominators and labeled intervals, and keep all calibration-only authority flags false.

## User Stories

1. As a supervisor operator, I want every held-out task to contain positive, negative, and public-pass hidden-fail controls, so that calibration catches both hollow success and over-rejection.
2. As an AutoResearch reviewer, I want no-regression to protect previously accepted oracle-positive cases, so that a stricter harness cannot hide true-accept loss while reducing false accepts.
3. As a measurement owner, I want held-out metrics separated from dev and best-of-K peaks, so that reported improvement is not selected from the same data used to tune behavior.
4. As a skeptical maintainer, I want sample sizes and confidence intervals in the report, so that small-corpus estimates are visibly uncertain.
5. As a gate designer, I want report-only invariants preserved, so that held-out calibration cannot create applyable policy proposals or advance supervisor gates.

## PRD Promise Contracts

P1. The corpus manifest rejects any held-out task missing a positive control, negative control, or public-pass hidden-fail trap.
P2. The paired report records replayable split metadata and separates held-out metrics from dev or in-sample metrics.
P3. No-regression findings protect prior true-positive accepted cases rejected by the new supervisor arm, while prior oracle-negative false accepts remain rejectable.
P4. Every acceptance arm reports n_bad, n_good, false-accept denominator, true-accept denominator, and a clearly labeled approximate confidence interval.
P5. Best-of-K or in-sample peak metrics cannot be labeled as held-out improvement evidence.
P6. Calibration outputs remain non-applyable and cannot mutate policy, advance gates, or create policy proposals.
P7. The first implementation tests exercise `validate_mergeability_corpus` and `run_paired_acceptance_pilot`, not helper-only internals.

## Implementation Decisions

Keep the mergeability bench as the deep module. Add only small public-report fields and fixture metadata, using existing task and candidate JSON contracts. Derive false-accept traps from candidate metadata and observed control kinds instead of introducing a new evaluator surface. Treat unavailable reviewer panels as unavailable measurements, not no-regression failures. Use a Wilson-style approximate interval because it is deterministic, dependency-free, and readable for small binary samples.

## Testing Decisions

The first RED tests target `validate_mergeability_corpus` for manifest rejection and `run_paired_acceptance_pilot` for reporting behavior. Tests must prove missing positive controls, missing negative controls, and missing false-accept traps are rejected through the corpus validator. Report tests must inspect the public paired report for held-out/dev separation, interval fields, honest best-of-K labeling, and the true-positive no-regression rule.

## Out of Scope

This slice does not run live candidate generation, claim production false-accept reduction, approximate maintainer mergeability, tune reviewer prompts, approve overlays, or make policy proposals. It does not replace the bench oracle with human maintainer labels, and it does not allow calibration-only results to become applyable evidence.
