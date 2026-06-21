## Problem Statement

Mergeability calibration currently records a public deterministic supervisor arm and a full-gate arm, but the full-gate path remains unavailable unless tests or callers inject a reviewer panel manually. Operators therefore cannot tell whether the configured independent reviewer roster changes false-accept behavior, because the calibration surface falls back to an unavailable panel instead of exercising the same reviewer aggregation machinery used by the supervisor workflow. This slice must wire a real configured reviewer-panel adapter into the mergeability measurement path while preserving oracle isolation and report-only scope.

## Solution

Add a narrow mergeability reviewer-panel adapter that converts each public reviewer packet into the existing independent reviewer request shape, invokes the configured reviewer roster through the existing reviewer registry seam, aggregates the results with the existing conservative panel evaluator, and returns a normalized panel result to the full-gate arm. The adapter must remain injectable for tests, expose an explicit unavailable result when reviewer infrastructure cannot run, and keep all hidden oracle fields outside prompts, packets, receipts, and transcripts. Calibration reports continue to label the result as non-applyable measurement evidence.

## User Stories

1. As an operator, I want mergeability calibration to exercise the configured reviewer panel, so that S_full is not silently marked unavailable when reviewer infrastructure is available.
2. As a reviewer, I want the mergeability packet to contain only public candidate evidence, so that my verdict cannot be influenced by hidden oracle labels or protected path content.
3. As a supervisor maintainer, I want reviewer failures to become explicit unavailable S_full measurements, so that missing reviewers never count as acceptances or improvements.
4. As a future evaluator, I want reviewer results and packet references recorded beside FAR and TAR rows, so that later audits can replay which panel decision changed each candidate outcome.

## PRD Promise Contracts

P1. Configured reviewer panel wiring: Calling the mergeability calibration with configured reviewer-panel mode uses the reviewer registry roster and conservative aggregation instead of the unavailable fallback when reviewers return verdicts.

P2. Oracle isolation: Reviewer packets, reviewer prompts, reviewer requests, panel receipts, and report rows exclude hidden tests, final scores, oracle labels, expected outcomes, hidden test commands, and protected path content.

P3. Fail-closed availability: Missing reviewer verdicts, reviewer infrastructure errors, or oracle-leak detections make S_full unavailable or rejected, never accepted, imputed, applyable, or converted into a policy mutation.

P4. Replayable measurement evidence: The report records reviewer packet references, reviewer result summaries, panel decisions, and S_probe versus S_full disagreement without changing the existing public-check arm.

## Implementation Decisions

The primary seam is the existing `run_paired_acceptance_pilot` interface because it is the operator-facing measurement boundary for fixture mergeability calibration. The implementation should add a small adapter or factory near the mergeability bench code, not duplicate reviewer aggregation logic. The adapter may accept injected reviewer adapters and runners for deterministic tests, while production construction should call `configured_reviewers`, `independent_reviewer_results_from_review_results`, and `evaluate_reviewer_panel`. The existing `_public_input_oracle_refs` leak detector remains the authority for public packet safety, and any leak blocks reviewer invocation. The current `supervisor_candidate_review` arm remains unchanged.

## Testing Decisions

Tests start at `run_paired_acceptance_pilot`, not at a helper-only adapter. The first RED proof should call the calibration boundary with configured-panel mode and fake reviewer adapters, then assert S_full is available, reviewer results are recorded, and S_probe output is unchanged. Later tests cover unavailable reviewer verdicts, oracle material leak blocking, and report-only invariants. Unit tests must fake the reviewer runners below the registry seam; no live Cursor, Codex, hidden oracle execution outside existing deterministic fixtures, or policy evolution may run by default.

## Out of Scope

This slice does not wire live SWE-bench generation, replace the self-report baseline, alter powered factorial statistics, promote any policy proposal, change reviewer roster defaults, or claim real-world improvement. It only makes the configured reviewer-panel path measurable for the existing calibration boundary under explicit report-only constraints.
