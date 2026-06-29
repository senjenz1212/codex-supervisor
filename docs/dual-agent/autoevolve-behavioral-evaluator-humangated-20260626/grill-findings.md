# PRD Grill Findings

## Verdict

Accepted after tightening the dependency framing. The implementation may harden AutoResearch evaluator selection and policy derivation now, but must not claim the "after real benchmark exists" execution condition because the scaled benchmark artifact is still blocked.

## Findings

### G1. The benchmark prerequisite is absent.

Finding: `docs/dual-agent/powered-real-benchmark-run-20260626/artifacts/powered-real-benchmark-execution-status.json` records a blocked scaled run, not a completed benchmark.

Resolution: keep this slice scoped to AutoResearch evaluator and human-gate hardening. The PRD states that benchmark evidence is not consumed and no real benchmark claim is made.

### G2. Defaulting to behavioral evaluation is necessary but not sufficient.

Finding: changing the default evaluator alone would be shallow if the evaluator still resolved candidates from source fixtures instead of the attempt worktree.

Resolution: the TDD plan includes an evaluator-boundary test proving that changing the attempt-worktree candidate changes the metric.

### G3. Replay-corpus must be rejected as adoption evidence, not deleted.

Finding: the replay-corpus evaluator can still be useful for explicit fixture replay, but its metric is not behavioral enough for policy adoption.

Resolution: preserve the file and reject records carrying that evaluator from policy derivation.

### G4. Benchmark reports need a hard derivation blocker.

Finding: the benchmark bridge is currently blocked by missing metrics and flags, but a future "accepted-looking" benchmark-promotion record should still not derive policy changes.

Resolution: the policy derivation boundary rejects benchmark-promotion-shaped records directly.

### G5. Human gating already exists but needs a targeted regression.

Finding: `_require_operator` already enforces approver and channel for application, but this slice needs an explicit adoption-path regression.

Resolution: add `test_adoption_requires_named_operator`.
