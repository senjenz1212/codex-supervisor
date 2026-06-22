## Problem Statement

SWE-bench diagnostic reports can only compare supervisor decisions against a real single-agent baseline when each baseline decision has replayable provenance. Gold-patch smoke, metadata self-reporting, legacy booleans, and malformed receipts must remain unavailable rather than quietly becoming accept-all evidence.

## Solution

Require produced baseline receipts on SWE-bench replay and official replay candidates before populating the single-agent baseline arm. The resolver verifies candidate id, candidate artifact hash, trusted decision source, model or provider evidence, runner label, prompt hash, and explicit accept or reject decision before allowing FAR, TAR, or matched-TAR comparisons.

## User Stories

1. As an operator, I want gold-patch smoke rows to remain baseline-unavailable, so that plumbing smoke cannot masquerade as a produced single-agent decision.
2. As an evaluator, I want valid replayed baseline receipts to populate a separate baseline arm, so that supervisor comparisons stop using metadata accept-all.
3. As a benchmark maintainer, I want hash-mismatched baseline receipts rejected, so that one candidate's receipt cannot be reused for another candidate.
4. As a reviewer, I want matched-TAR comparisons blocked when baseline evidence is unavailable, so that diagnostic reports do not manufacture a comparison denominator.

## PRD Promise Contracts

P1. Gold-patch or smoke-only candidates without produced baseline receipts stay unavailable in the single-agent baseline arm.
P2. Valid produced baseline receipts populate baseline FAR, TAR, provenance fields, and frozen decisions without using metadata accept-all.
P3. Hash mismatches, malformed receipts, untrusted decision sources, and missing receipts mark the baseline unavailable with an explicit reason.
P4. Matched-TAR comparisons refuse unavailable baseline arms and keep diagnostic reports non-applyable with policy mutation disabled.
P5. Supervisor-arm live generation cannot supply or smuggle single-agent baseline receipts for supervisor-produced candidates.

## Implementation Decisions

The public seam is the SWE-bench replay and official replay report path, because those reports are what downstream diagnostics and operators inspect. Baseline receipt normalization remains centralized in the shared mergeability resolver, while the SWE-bench bridge only forwards candidate-level receipt keys and records the resulting frozen decision rows. Live runners may synthesize or forward produced baseline receipts only from the baseline arm, never from the supervisor arm.

## Testing Decisions

Tests drive official replay and official live runners through their public report interfaces. The first tests assert gold smoke without a receipt, valid replayed receipts, hash mismatch, live baseline generator receipts, and supervisor-arm receipt smuggling through emitted rows, arm summaries, and matched-TAR status. Helper-only receipt checks are secondary to the report behavior.

## Out of Scope

This slice does not run live paid benchmark generation, does not prove reviewer quality, does not promote policy proposals, and does not make human-mergeability claims. It also does not create a powered corpus; it only prevents diagnostic baselines from being silently imputed.
