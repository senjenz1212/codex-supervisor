## Problem Statement

SWE-bench mergeability measurement can still treat missing baseline evidence or legacy self-report as if a single-agent baseline accepted the candidate. That undermines the core comparison because a metadata or self-report fallback can look like produced evidence. Operators need all mergeability paths to share one baseline contract before trusting any supervisor-versus-baseline number.

## Solution

Require replayable produced-baseline receipts for SWE-bench baseline availability. The bridge and fixture, replay, and live runners should reuse the existing mergeability baseline normalizer. Missing, malformed, legacy boolean, hash-mismatched, or self-report-only rows become unavailable, never accepted. Valid receipts preserve candidate id, artifact hash, decision source, producer, prompt hash, and accept or reject.

## User Stories

1. As an operator, I want baseline rows to mean produced single-agent decisions, so that reports do not compare against accept-all metadata.
2. As an evaluator, I want missing receipts marked unavailable, so that matched true-accept metrics refuse unsupported comparisons.
3. As a reviewer, I want self-report preserved only as calibration context, so that old manifests remain inspectable without becoming evidence.
4. As an implementer, I want local and SWE-bench paths to share a receipt normalizer, so that future baseline changes stay localized.

## PRD Promise Contracts

P1. SWE-bench baseline rows must validate replayable produced-baseline evidence before they can be available.
P2. Missing or self-report-only baseline evidence must become unavailable and must never synthesize acceptance.
P3. Reports must expose produced baseline provenance while keeping calibration outputs report-only and non-applyable.

## Implementation Decisions

The main seam is the SWE-bench mergeability bridge and runner interface, not a standalone helper. The implementation should import and reuse the local mergeability produced-baseline normalizer. Candidate manifests may carry produced baseline receipts under explicit receipt fields. Live generation may attach a receipt only when the generator provides an explicit accept or reject decision.

## Testing Decisions

Tests begin at public boundaries: direct bridge report construction, fixture runner execution, replay runner execution, and live runner execution. The first proof verifies a legacy self-report candidate becomes baseline-unavailable without a produced receipt. Additional tests prove valid receipts populate report rows, legacy booleans are rejected, and live generation no longer invents baseline acceptance.

## Out of Scope

This slice does not fetch official SWE-bench datasets, run Docker oracles, power the evaluation, change reviewer-panel aggregation, mutate policy, or claim supervisor improvement. It only repairs baseline evidence semantics for future measurement.
