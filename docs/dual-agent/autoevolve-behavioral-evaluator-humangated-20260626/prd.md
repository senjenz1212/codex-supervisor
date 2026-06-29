# PRD: Auto-Evolve Behavioral Evaluator, Human-Gated

Task id: `autoevolve-behavioral-evaluator-humangated-20260626`

## Problem Statement

The AutoResearch auto-evolve loop can currently default to a replay-corpus evaluator that mostly checks fixture evidence existence instead of grading the behavioral effect of the candidate. That makes the adoption signal too easy to satisfy without proving the candidate changed the evaluated behavior.

The real SWE-bench Pro benchmark remains operator-facing evidence only. The prior powered-real-benchmark slice records the current benchmark execution as blocked: no scaled powered artifact exists yet. This slice therefore hardens the AutoResearch evaluator and policy-gating path without claiming that a real benchmark has completed and without building any autonomous benchmark-to-policy bridge.

## Solution

Point default AutoResearch execution at the local behavioral mergeability evaluator, preserve hash-pinned evaluator execution, and reject replay-corpus fixture metrics as policy-derivation/adoption evidence. Keep benchmark-derived reports out of policy derivation even if a future report tries to look applyable. Keep proposal application human-gated through named-operator approval only.

## PRD Promise Contracts

### P1. The loop evaluates real behavior, not fixture existence.

Public boundary: `resolve_evaluator_defaults` and the evaluator executable path used by `run_autoresearch_fixture(..., execution_mode="live")`.

Chosen seam: default unresolved AutoResearch experiments to `supervisor/autoresearch/evaluators/mergeability_bench.py`, and ensure the evaluator resolves candidate artifacts from the attempt worktree before falling back to source fixtures.

Allowed: candidate-vs-empty-floor and candidate-vs-candidate deltas move when the candidate artifact changes; execution records `metric_source="evaluator_execution"` with hash-pinned evaluator provenance.

Forbidden: using `supervisor/autoresearch/evaluators/replay_corpus.py` as an adoption signal; a candidate artifact that is listed in the attempt but not actually read by the evaluator; helper-only tests that never execute the evaluator boundary.

### P2. Adoption stays human-gated; benchmark evidence is operator-facing only.

Public boundary: `derive_policy_evolution_proposals_from_report`, `report_contains_derivable_policy_record`, and `approve_policy_proposal`.

Chosen seam: policy derivation accepts only AutoResearch evaluator-execution records that are not benchmark-promotion records and not replay-corpus fixture metrics; policy application still calls `_require_operator`.

Allowed: AutoResearch may draft proposals from accepted behavioral evaluator records; a named operator may approve or deny; benchmark reports may be linked as evidence for operator review.

Forbidden: deriving policy proposals from benchmark-promotion reports; autonomous policy mutation; missing approver approval; flipping benchmark `improvement_claim_allowed` or authority flags to justify adoption.

## User Stories

1. As an auto-evolve maintainer, I want unresolved experiments to run a behavioral evaluator, so that accepted evidence reflects behavior rather than fixture presence.
2. As a policy maintainer, I want replay-corpus metrics rejected for adoption, so that fixture existence cannot promote a policy overlay.
3. As a benchmark operator, I want SWE-bench Pro benchmark artifacts kept out of policy derivation, so that benchmark evidence remains review material, not an actuator.
4. As an operator, I want proposal application to require my named approval, so that no automated loop mutates policy defaults without a human gate.
5. As a reviewer, I want tests at the evaluator and policy-derivation boundaries, so that the proof covers the same seams the loop uses.
6. As a future implementer, I want the absent real benchmark recorded as a dependency caveat, so that this slice does not pretend benchmark execution is complete.

## Implementation Decisions

- Preserve hash-pinned evaluator execution; only change the default selected evaluator for unresolved experiments.
- Keep the legacy replay-corpus evaluator file for explicit fixture replay and old tests, but mark it non-derivable for policy adoption when it appears in report records.
- Make the mergeability evaluator prefer candidate paths inside `--attempt-worktree`; this is the small seam that proves the evaluator reads the candidate artifact under test.
- Add record-level evaluator provenance to AutoResearch validation reports so policy derivation can reject fixture-only evaluator records.
- Add a benchmark-promotion record blocker in policy derivation; benchmark evidence can be surfaced to operators but cannot become the derivation source.
- Do not consume the powered real benchmark artifact here. The current artifact is blocked, and the benchmark remains outside the AutoResearch adoption path.

## Testing Decisions

- First RED hits the evaluator executable boundary: changing the candidate artifact in the attempt worktree changes the mergeability score.
- The default-resolution RED checks unresolved experiments select the behavioral evaluator and `mergeability_score`.
- The replay-corpus rejection RED uses an otherwise applyable policy record with `evaluator_ref` set to replay-corpus and proves no proposal derives.
- The benchmark non-routing RED uses an otherwise applyable benchmark-promotion-shaped record and proves no proposal derives.
- The human-gate RED derives a draft proposal from a behavioral record, then proves approval without a named operator raises.
- No live model, Docker, SWE-bench Pro oracle, or scaled benchmark run is executed in unit tests.

## Out Of Scope

- Running the scaled real benchmark.
- Building any autonomous benchmark-to-AutoResearch promotion bridge.
- Changing benchmark authority flags.
- Changing reviewer panel, powered-factorial, or SWE-bench Pro corpus generation logic.

## Current Dependency Status

The prior `powered-real-benchmark-run-20260626` packet records `status="blocked"` for the scaled real benchmark. This slice can still land because it hardens the AutoResearch adoption path and explicitly does not consume benchmark output.
