# PRD Grill Findings

## Findings

### Finding 1: Matched True-Accept Must Be Blocking For Comparisons

status: resolved
severity: high
question: Could the report compute false-accept deltas when true-accept differs across arms?
resolution: The PRD now requires a matched true-accept status that refuses unmatched comparisons instead of computing a misleading improvement.

### Finding 2: Oracle Ceiling Needs Explicit Anti-Promotion Language

status: resolved
severity: high
question: Could oracle-ceiling performance be promoted as supervisor improvement?
resolution: The PRD marks oracle ceiling as oracle-coupled and non-promotable, and the TDD plan includes a public-boundary anti-promotion test.

### Finding 3: Applyable Evidence Is Not The Same As Policy Mutation

status: resolved
severity: high
question: Could powered evidence mutate policy without a separate operator proposal?
resolution: The PRD separates `metric_applyable=true` from `default_change_allowed`, `policy_mutated`, and `gate_advanced`, which must remain false.

### Finding 4: Reviewer Analysis Must Handle Missing Reviewer-Level Data

status: resolved
severity: medium
question: Could leave-one-reviewer-out analysis be fabricated when reviewer rows are missing?
resolution: The PRD requires unavailable status when reviewer-level decisions are missing and only reports marginal effect or correlation when evidence exists.

## Decision

No open findings remain.

## Stage Receipt

skill: grill-with-docs
status: passed
output: docs/dual-agent/mergeability-powered-factorial-eval-20260619/source/grill-findings.md
