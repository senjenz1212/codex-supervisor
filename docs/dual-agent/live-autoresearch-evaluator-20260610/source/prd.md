# PRD: Live AutoResearch Evaluators

## Problem Statement

Supervisor AutoResearch currently replays JSON fixtures whose `metric_trials` can be hand-entered. That makes a report look quantitative even when no evaluator actually ran, no attempt worktree was isolated, and no runtime evidence can be replayed. The gap is dangerous because the supervisor is meant to improve itself through evidence, not through attractive fixture numbers.

## Solution

Add an opt-in live execution lane where `evaluator_ref` names a local script and `evaluator_hash` pins the exact bytes allowed to run. The supervisor runs the evaluator `k_trials` times against an isolated attempt worktree, computes `metric_trials` from evaluator stdout, records an evaluator-run artifact hash, and still treats the result as report-only evidence.

## User Stories

- As an operator, I can run a live AutoResearch experiment and see metrics that came from an executed evaluator, not from the fixture.
- As a reviewer, I can inspect the evaluator script hash, trial artifact, metric list, and IQR before trusting the report.
- As a maintainer, I can reject hash mismatches, dangling evidence references, and mutable-path escapes without changing production defaults.
- As a gate owner, I can keep AutoResearch advisory: it cannot advance gates or mutate policy by itself.

## PRD Promise Contracts

P1. Executable evaluator contract: `run_autoresearch_fixture(...)` and the CLI execute a local hash-pinned evaluator only when live mode is explicitly allowed. Hash mismatch blocks execution and produces rejected evidence.

P2. Trial metrics are runtime-native: `k_trials` evaluator executions compute `metric_trials`, median, IQR, and stability flags. Fixture-provided metric numbers without evaluator provenance are rejected.

P3. Attempt isolation is scoped: evaluator execution uses an isolated attempt worktree and mutable-path checks. Side effects outside `mutable_paths` become validation errors and never leak into the source checkout.

P4. Gaming flags are first-class: validation reports expose `evaluator_not_executed`, `zero_variance_trials`, `dangling_evidence_ref`, and `evaluator_hash_mismatch` where applicable.

P5. Report-only invariants hold: every live report keeps `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`; evaluator results remain evidence, not gate authority.

## Implementation Decisions

The live lane stays in `supervisor/autoresearch/` and is invoked through `run_autoresearch_fixture(...)`. The evaluator contract is stdout JSON with a numeric metric value. Execution artifacts are written under the requested output directory, and durable job-shaped events are emitted through the existing state event writer.

## Testing Decisions

Tests exercise public boundaries: direct `validate_attempt(...)`, orchestrated `run_autoresearch_fixture(...)`, and the CLI. Negative tests cover fixture-only metrics, hash mismatch before execution, mutable path escape, dangling refs, zero-variance flagging, and report-only invariants.

## Out Of Scope

This slice does not add automatic patch application, default policy changes, gate authority changes, model-driven hypothesis generation, new infrastructure, fan-out default changes, or reviewer default changes.
