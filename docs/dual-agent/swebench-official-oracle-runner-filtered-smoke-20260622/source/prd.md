## Problem Statement

The current SWE-bench replay path is not yet a trustworthy executable benchmark because the official replay CLI can route into the mergeability report without an oracle runner. The operator needs a tiny, filtered SWE-bench smoke path that proves Docker or official-harness-equivalent grading actually ran, freezes public decisions before oracle labels, and refuses to emit FAR/TAR when the adapter is only a label.

## Solution

Add an official replay CLI contract that requires an explicit oracle adapter, passes an oracle runner into the replay runner, filters selected SWE-bench instances before prediction validation, and records official oracle receipts. The solution keeps injected fake runners available for tests while making production smoke runs prove command execution, label validation, hidden-field isolation, and report-only invariants.

## User Stories

1. As an operator, I want the official replay command to fail when no oracle adapter is configured, so that a report cannot pretend to be officially graded.
2. As an evaluator, I want selected instance IDs and deterministic limits applied before prediction validation, so that tiny smoke runs do not require predictions for every dataset row.
3. As a reviewer, I want public packets and frozen decisions to exclude hidden oracle fields, so that the supervisor decision path remains independent of FAIL_TO_PASS and PASS_TO_PASS labels.
4. As a maintainer, I want oracle receipts with command hashes, return codes, harness metadata, and artifact paths, so that benchmark evidence can be replayed and audited.
5. As a product owner, I want all outputs marked report-only, so that a plumbing smoke cannot mutate policy or claim human mergeability.

## PRD Promise Contracts

P1. The official replay CLI refuses to emit metrics unless an explicit Docker, official harness, or validated official-equivalent oracle runner is provided.
P2. The replay selection layer filters by repeatable instance IDs or deterministic limits before enforcing prediction coverage or reporting selected rows.
P3. The public decision layer excludes patch, test_patch, FAIL_TO_PASS, PASS_TO_PASS, final_score, oracle_accept, and expected_outcome from packets, generator inputs, reviewer packets, and frozen decisions.
P4. The oracle execution layer writes receipts after frozen decisions exist, including command, return code, stdout and stderr hashes, evaluator metadata, FAIL_TO_PASS status, PASS_TO_PASS status, and artifact references.
P5. The report layer keeps metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced false for this filtered smoke path.

## Implementation Decisions

The main seam is the official replay CLI interface because it is the boundary the operator invokes and the point where adapter configuration, instance filtering, prediction coverage, and report writing meet. The oracle runner remains injectable so public-boundary tests can use a fake official adapter without Docker, while the production adapter records enough metadata to distinguish real harness execution from a descriptive label. Filtering belongs before prediction validation to support one-to-three instance smoke runs.

## Testing Decisions

Testing starts at the CLI public boundary rather than helper functions. The first tests exercise official replay with and without oracle configuration, selected instance IDs, deterministic limits, frozen-decision ordering, hidden-field exclusion, and official-equivalent label validation failure. Helper tests may follow only after these public-boundary cases demonstrate the executable path and report-only invariants.

## Out of Scope

This slice does not run a powered benchmark, does not claim maintainer mergeability, does not enable live generation, does not fix reviewer-panel availability, and does not create an applyable policy proposal. SWE-bench Verified remains a plumbing smoke corpus only; representative measurement belongs to later Pro or contamination-resistant evaluation.
