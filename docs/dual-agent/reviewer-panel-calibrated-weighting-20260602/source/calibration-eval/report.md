# Reviewer Panel Eval Report

- schema_version: `reviewer-panel-eval/v1`
- execution_mode: `fixture_replay`
- task_count: 5
- reviewer_count: 2
- policy_change_allowed: `false`

## Per Reviewer

### independent-reviewer-0
- false_accept_rate: 1/1 = 1.000
- false_block_rate: 0/4 = 0.000
- unavailable_rate: 0/5 = 0.000
- total_cost_usd: 0.000000
- avg_latency_ms: 0.000

### independent-reviewer-1
- false_accept_rate: 0/1 = 0.000
- false_block_rate: 0/4 = 0.000
- unavailable_rate: 0/5 = 0.000
- total_cost_usd: 0.000000
- avg_latency_ms: 0.000

## Pairwise

### independent-reviewer-0__independent-reviewer-1
- agreement_rate: 4/5 = 0.800
- combined_failure_jaccard: 0.000
- block_decision_correlation: not_applicable
- wrong_decision_correlation: not_applicable
