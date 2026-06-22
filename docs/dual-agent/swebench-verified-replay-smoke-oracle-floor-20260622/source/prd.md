## Problem Statement

Supervisor mergeability has an official SWE-bench replay path, but it has not yet produced a tiny real-instance smoke artifact proving that selected SWE-bench Verified rows can be evaluated with the official oracle after decisions are frozen. Without that run, the benchmark remains wired but unexercised, and S_probe evidence cannot be distinguished from panel evidence or contamination-prone benchmark claims.

## Solution

Run a deliberately small SWE-bench Verified replay smoke through the official replay path using explicit selected instance identifiers or a deterministic limit. The run must write predictions, frozen decisions, oracle receipts, and bridge FAR/TAR rows while labeling Verified as plumbing smoke only. Full-panel metrics remain unavailable unless the reviewer roster is fully valid, and every output remains report-only.

## User Stories

1. As a benchmark operator, I want a tiny official replay smoke, so that I can verify the oracle and S_probe floor before live spending.
2. As a supervisor maintainer, I want selected rows recorded deterministically, so that replay evidence is inspectable and not a hidden broad benchmark sweep.
3. As a reviewer-gate owner, I want full-panel metrics suppressed when roster availability is invalid, so that partial infrastructure cannot travel as panel quality.
4. As an evaluator, I want Verified labeled as plumbing smoke only, so that no one mistakes test-pass replay for human mergeability.
5. As an operator, I want report-only invariants preserved, so that smoke artifacts cannot mutate policy or unlock auto-evolution.

## PRD Promise Contracts

P1. The smoke command writes replay artifacts with at least one selected instance, at least one candidate, frozen decisions, oracle receipts, and a bridge report.
P2. The report records selected instance identifiers or deterministic limit metadata and evaluates only those selected rows.
P3. Official oracle execution happens after frozen decisions are persisted, and receipts include command or adapter evidence plus artifact hashes.
P4. SWE-bench Verified output is labeled plumbing_smoke_only and cannot be reported as powered improvement or maintainer mergeability.
P5. Full-panel metrics are unavailable unless full reviewer roster availability is valid; Codex-only or missing-roster evidence is labeled separately.
P6. Report-only invariants remain false for metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced.

## Implementation Decisions

Use the official replay interface that Slice 1 added rather than creating another benchmark runner. The smoke should prefer a tiny checked, explicit selection because current replay must not require predictions for non-selected records. The implementation should reuse existing oracle-runner seams for tests, public artifact leak checks, and the S_probe bridge summary. If real Docker execution is unavailable in this environment, the run must fail or mark unavailable honestly instead of substituting a label-only oracle.

## Testing Decisions

Tests start at the CLI or official replay public boundary. The first red test verifies that a smoke invocation writes official_replay_report.json with nonzero selected instance and candidate counts while preserving frozen-before-oracle proof. Follow-on tests cover selection metadata, oracle receipt presence, Verified plumbing-smoke labeling, full-panel unavailability when the roster is not clean, and the absence of policy proposals.

## Out of Scope

This slice does not claim SWE-bench Pro performance, does not run live generation, does not spend live model budget, does not evaluate human maintainer mergeability, and does not create or approve policy changes. Powered sample sizing, live official CLI generation, and promotion guardrails belong to later slices.
