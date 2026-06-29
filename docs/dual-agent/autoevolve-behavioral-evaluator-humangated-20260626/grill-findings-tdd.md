# TDD Grill Findings

## Verdict

Accepted. The first RED hits the evaluator executable boundary, not a helper. Later REDs hit policy derivation and approval, which are the adoption seams named by the PRD.

## Findings

### T1. A default-path assertion alone would be tautological.

Resolution: the first test proves worktree candidate content changes the score.

### T2. Replay-corpus rejection needs an otherwise applyable record.

Resolution: the test fixture should include positive metric delta, empty-floor evidence, quality controls, and candidate artifacts. Only the replay-corpus provenance should block it.

### T3. Benchmark non-routing needs a future-proof blocker.

Resolution: test an otherwise applyable benchmark-promotion-shaped record, not only the currently blocked bridge output.

### T4. Human gating must prove application, not only proposal shape.

Resolution: derive a proposal and call `approve_policy_proposal` with a missing approver to prove application remains gated.
