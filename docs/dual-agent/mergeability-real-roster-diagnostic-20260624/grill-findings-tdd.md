# TDD Grill Findings

## Finding 1: First tests must hit the public seam

Status: resolved. The TDD plan starts with `run_mergeability_reviewer_roster_diagnostic`.

## Finding 2: Helper math must not outrun observable behavior

Status: resolved. Pairwise overlap and effective-vote behavior are asserted through report fields.

## Finding 3: Positive authority case still needs report-only invariants

Status: resolved. The final RED test requires `metric_applyable=false`, `improvement_claim_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.
