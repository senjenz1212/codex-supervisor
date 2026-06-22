## Problem Statement

The SWE-bench diagnostic runner must not convert infrastructure failures into evidence that a candidate patch failed the oracle. Operators need reports that distinguish real unresolved benchmark outcomes from Docker, harness, timeout, dataset, dependency, missing-patch, or missing-report failures, because false FAR/TAR denominators would make the diagnostic instrument look more precise than it is.

## Solution

The official oracle adapter will return an explicit unavailable state with a stable reason and receipt whenever the adapter cannot obtain a valid official harness report. The mergeability bridge and official replay summary will propagate that state, suppress or exclude affected FAR/TAR/FRR metrics, and keep report-only invariants intact while preserving valid failing official reports as ordinary oracle failures.

## User Stories

1. As an operator running SWE-bench diagnostics, I want harness infrastructure failures labeled unavailable, so that they do not masquerade as bad candidate patches.
2. As a reviewer auditing evidence quality, I want receipts to include command hashes, return codes, artifact references, and unavailable reasons, so that I can tell whether the official oracle actually ran.
3. As a benchmark maintainer, I want valid unresolved official reports to remain fail/fail outcomes, so that unavailable semantics do not erase real benchmark failures.
4. As a supervisor gate reviewer, I want unavailable oracle rows to suppress or remove metrics with explicit reasons, so that calibration reports cannot claim precision from missing labels.

## PRD Promise Contracts

P1. Adapter infrastructure failures produce fail_to_pass_status and pass_to_pass_status values of unavailable with a specific oracle_unavailable_reason and receipt.
P2. Valid official harness reports that contain unresolved tests still produce ordinary fail or pass labels rather than being relabeled unavailable.
P3. Official replay reports suppress or exclude FAR/TAR/FRR metrics when oracle labels are unavailable and expose metrics_unavailable_reasons.
P4. Receipt validation accepts unavailable adapter receipts only when they preserve required command metadata and unavailable reason evidence.
P5. Report-only invariants remain false for improvement claims, human mergeability claims, policy mutation, and gate advancement.

## Implementation Decisions

The public seam is the official replay runner that receives adapter outcomes and writes bridge reports, oracle outputs, and receipt validation summaries. The adapter failure helper will be the single deep module for infrastructure-failure classification, while bridge interpretation will normalize unavailable outcomes before arm metrics are computed. This avoids spreading unavailable logic across individual tests or report consumers.

## Testing Decisions

Tests must start at public runner and adapter boundaries: official replay with an unavailable adapter, official adapter nonzero subprocess return, official adapter timeout or missing report where practical, and a valid unresolved report. Helper tests are not enough because the risk is denominator corruption in emitted reports, not a private function returning a string.

## Out of Scope

This slice does not add live generation, prove reviewer-panel quality, populate produced baseline decisions, or make powered improvement claims. It does not weaken the official smoke artifact or reinterpret SWE-bench Verified as human mergeability evidence. It only corrects oracle-label availability semantics for diagnostic measurement.
