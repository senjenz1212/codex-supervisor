## Problem Statement

The mergeability reviewer panel currently receives public review output, but the execution evidence is embedded in the generic public review payload rather than surfaced as a first-class, stable evidence contract. That makes it harder to distinguish execution-grounded reviewer decisions from text-only judgments, and it increases the risk that future benchmark reports overclaim what the panel actually saw.

## Solution

Add a public-only execution evidence block to mergeability reviewer packets. The block must summarize patch or file-overlay applicability, public candidate tests, reverse-classical test quality, public lint/build evidence, scope/locality, protected-path exclusion, and hidden-oracle exclusion without exposing hidden oracle material.

## User Stories

1. As a benchmark operator, I want reviewer packets to show public execution evidence explicitly, so that I can audit whether a reviewer decision was grounded in executable signals.
2. As a supervisor reviewer, I want candidate tests and reverse-classical results summarized separately, so that weak or non-discriminating tests are not mistaken for real behavioral evidence.
3. As a benchmark maintainer, I want hidden oracle material excluded from every evidence block, so that reviewers cannot become coupled to the answer key.
4. As an evaluator author, I want SWE-bench replay packets to carry patch-apply and public-probe receipts, so that real-instance runs preserve the same public evidence contract.

## PRD Promise Contracts

- P1: Full-gate reviewer packets expose a `public_execution_evidence` block at the reviewer packet boundary.
- P2: The evidence block includes public candidate-test, reverse-classical, lint/build, scope/locality, protected-path exclusion, and hidden-oracle exclusion summaries.
- P3: SWE-bench reviewer packets expose patch-apply and public-probe execution evidence.
- P4: Hidden oracle keys, hidden tests, final scores, oracle labels, expected outcomes, and protected path content are not exposed.
- P5: Existing report-only and full-panel calibration behavior remains unchanged.

## Implementation Decisions

- Use the existing reviewer packet builders as the public boundary.
- Reuse existing public review command receipts instead of adding a second execution path.
- Treat local mergeability fixture candidates as file-overlay candidates; patch apply is marked not applicable there.
- Treat SWE-bench-shaped replay candidates as patch candidates; patch apply receipts are included in the execution evidence.
- Use stable command payloads so packet hashes remain deterministic across command durations.

## Testing Decisions

- Start with public-boundary tests that inspect reviewer packets produced by `run_paired_acceptance_pilot` and `swebench_mergeability_fixture_runner`.
- Assert the new evidence block is present and public-only.
- Assert reverse-classical test quality is recorded for a known-good fixture candidate.
- Assert SWE-bench packets include patch-apply and public-probe evidence without forbidden oracle strings.

## Out of Scope

- Adding reviewer-family provenance, generator-disjoint guards, abstention labels, or real benchmark execution.
- Changing panel aggregation semantics.
- Making calibration evidence applyable.

