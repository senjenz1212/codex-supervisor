# TDD Grill Findings

## Findings

### Finding 1: The First Test Must Be Report-Level

status: resolved
severity: high
question: Does the TDD plan begin at the public report boundary rather than helper-only math?
resolution: The first RED test now calls the factorial report interface and asserts labeled arms plus replay metadata.

### Finding 2: Same Candidate Pool Needs Its Own Negative Test

status: resolved
severity: high
question: Does the plan prove all compared arms use the same candidate pool?
resolution: `test_powered_factorial_uses_same_candidate_pool_across_arms` introduces a missing candidate and requires comparison refusal before metric deltas are computed.

### Finding 3: Powered Evidence Must Not Become Policy Authority

status: resolved
severity: high
question: Does the plan separate applyable metric evidence from automatic policy mutation?
resolution: `test_powered_threshold_met_may_allow_metric_but_never_mutates_policy` requires metric applyability to remain separate from `default_change_allowed`, `policy_mutated`, and `gate_advanced`.

### Finding 4: Reviewer Unavailability Needs A Public Boundary Assertion

status: resolved
severity: medium
question: Does unavailable reviewer evidence block the full-stack claim at the public boundary?
resolution: `test_reviewer_unavailable_blocks_full_stack_claim` requires unavailable reviewer evidence to block the full-stack claim, keep `metric_applyable=false`, keep `improvement_claim_allowed=false`, and produce no applyable policy proposal rather than copying an acceptance from another arm.

## Decision

No open findings remain.

## Stage Receipt

skill: grill-with-docs
status: passed
output: docs/dual-agent/mergeability-powered-factorial-eval-20260619/source/grill-findings-tdd.md
