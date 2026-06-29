# AutoResearch Empty-Floor Evaluator PRD

Task id: `autoresearch-empty-floor-evaluator-20260626`

## Problem Statement

The AutoResearch learning loop can start an attempt with `metric_before=0.0` as a seed value, but the live evaluator path does not currently replace that seed with a measured empty-floor run. The evaluator returns live `metric_trials`, evaluator refs, and hashes, and the orchestrator propagates those into the attempt, but the before/after/delta fields are not execution-derived. As a result, `empty_floor_comparison()` can be `None` in a real run, or worse, a seed can be mistaken for evidence.

This is the genuine auto-evolve gap for evaluator execution. It is independent of the mergeability benchmark and does not require a benchmark-to-policy bridge.

## Solution

Run the evaluator against a stripped or empty policy overlay candidate before candidate trials, treat that metric as the measured empty floor, and carry the resulting `metric_before`, `metric_after`, and `metric_delta` through durable execution, orchestration, validation, reporting, and draft policy derivation. The evaluator run is the execution evidence. Tests should fake live infrastructure below the public boundary, not mock the public boundary itself.

## User Stories

1. As an AutoResearch operator, I want an empty-floor metric measured by the evaluator, so that a draft proposal cannot claim improvement from a seed value.
2. As an AutoResearch operator, I want live before/after/delta fields in the report, so that `empty_floor_comparison()` reflects execution evidence.
3. As an AutoResearch operator, I want derivable proposals to remain draft-only, so that a measured improvement does not mutate policy without approval.
4. As a reviewer, I want the empty-floor evidence stored in evaluator artifacts, so that I can inspect what was measured.
5. As a maintainer, I want durable job resume payloads to preserve before/after/delta, so that idempotent evaluator recovery does not lose the measured floor.

## Implementation Decisions

- The primary interface is `run_evaluator_trials`; it should return execution-derived metric fields in `EvaluatorExecutionResult`.
- The empty-floor run is only measured for policy overlay candidate artifacts. Non-policy attempts must not fabricate an empty-floor comparison.
- The empty-floor worktree is derived from the live attempt worktree and strips the candidate policy overlay artifact to empty content before executing the evaluator.
- Candidate `metric_after` is the median of the live candidate trial metrics, matching the existing report semantics.
- Durable job terminal payloads preserve `metric_before`, `metric_after`, and `metric_delta` so resumed jobs and orchestrator replacement see the same execution evidence.
- `run_autoresearch_fixture` propagates the execution-derived fields into the attempt before validation.
- `derive_policy_evolution_proposals_from_report` remains draft-only. All authority flags stay false and operator approval remains required.

## Testing Decisions

- First RED is at `run_evaluator_trials`, not a helper. It proves a real evaluator process is executed against a stripped overlay and writes `empty_floor_metric` evidence.
- Propagation is tested through `run_autoresearch_fixture`, because that is the public live path that replaces attempts after durable execution.
- Draft derivation is tested end-to-end with a live evaluator, validation, report creation, and policy proposal derivation. The evaluator process is a test fixture below the public boundary.
- Fixtures may create fake evaluator scripts and fake `State`, but they must not replace `run_evaluator_trials`, `run_durable_evaluator_trials`, `run_autoresearch_fixture`, or `derive_policy_evolution_proposals_from_report`.

## PRD Promise Contracts

### P1. Empty-Floor Is Measured, Not Seeded

Public boundary: `run_evaluator_trials` / `run_durable_evaluator_trials`

Chosen seam: a real empty-overlay evaluator run populates `empty_floor_metric` in evaluator execution evidence.

Allowed outcomes: a measured floor from evaluator execution; `metric_before` equals that measured floor; the 0.0 generator seed is overwritten when a measured floor exists.

Forbidden outcomes: the 0.0 seed flowing through as the floor; `_control_metric_before` treating a pending seed as execution evidence.

### P2. Live Execution Propagates Real Metric Before/After/Delta

Public boundary: `run_autoresearch_fixture` live execution path, especially the `replace(attempt, ...)` call after durable execution

Chosen seam: durable evaluator result payload carries `metric_before`, `metric_after`, and `metric_delta`, and the orchestrator propagates those fields before validation.

Allowed outcomes: `empty_floor_comparison()` is non-`None` from a real live run and contains evaluator-backed before/after/delta values.

Forbidden outcomes: fixture values standing in for execution; validation recomputing from a seed because the live execution payload omitted the fields.

### P3. Live Run Derives Draft Proposal End-to-End

Public boundary: `derive_policy_evolution_proposals_from_report`

Chosen seam: a real `--allow-live` style fixture run produces a report record that the existing deriver can turn into a draft proposal.

Allowed outcomes: `derived_policy_proposals[*].status == "draft"`; `requires_operator_approval` remains true; default change, automatic mutation, and gate advancement all remain false.

Forbidden outcomes: autonomous apply; authority flag flips; benchmark promotion bridge work; mutating `.supervisor/policy-overlay.yaml` without explicit operator approval.

## Out of Scope

- The mergeability benchmark and any autonomous benchmark-to-policy bridge.
- Changing policy approval semantics.
- Making evaluator-quality controls optional for policy derivation.
- Applying policy proposals automatically.

## Source Evidence

- `supervisor/autoresearch/generator.py:499-516`: attempts are seeded with `metric_before=0.0`.
- `supervisor/autoresearch/evaluator.py:39-45`: public evaluator boundary.
- `supervisor/autoresearch/evaluator.py:779-785`: control baseline fallback currently can use attempt `metric_before`.
- `supervisor/autoresearch/orchestrator.py:120-145`: durable execution output currently replaces trials, refs, quality, errors, cost, and wall time.
- `supervisor/autoresearch/schema.py:289-298`: `empty_floor_comparison()` requires before, after, and delta.
- `supervisor/autoresearch/policy_evolution.py:522-544`: policy derivation requires evaluator execution and unchanged authority flags.
- `supervisor/autoresearch/policy_evolution.py:694-721`: policy derivation requires an evaluator-backed empty-floor win.
