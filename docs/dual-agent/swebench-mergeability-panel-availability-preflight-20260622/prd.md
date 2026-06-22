## Problem Statement

The SWE-bench mergeability runner can invoke a configured reviewer panel, but missing reviewer verdicts can look like ordinary panel rejection unless availability is recorded separately. Operators need S_full to distinguish infrastructure absence, missing roster coverage, and quality rejection before any diagnostic number is trusted.

## Solution

Add a configured reviewer-panel preflight record to the SWE-bench replay and official replay reports. The preflight will summarize available reviewers, missing reviewers, reviewer result evidence, transcript and output hashes, failure classification, and full-roster availability. S_full remains unavailable whenever the configured roster is incomplete.

## User Stories

1. As an operator, I want missing reviewer verdicts labeled unavailable, so that S_full does not claim a full-panel quality decision.
2. As a reviewer, I want transcript and output hashes for reviewer attempts, so that infrastructure failures can be audited without exposing hidden oracle material.
3. As a benchmark maintainer, I want full-roster quality rejects separated from infrastructure failures, so that reviewer quality can be measured only when the panel actually ran.
4. As a supervisor gate, I want unconfigured panel paths marked uninvoked, so that unavailable defaults are not confused with configured-panel evidence.

## PRD Promise Contracts

P1. Unconfigured SWE-bench panel paths report an uninvoked preflight and keep S_full unavailable.
P2. Configured-panel results with missing reviewers or missing roster evidence report an unavailable classification and keep S_full unavailable.
P3. Configured-panel full-roster quality rejects remain available quality decisions rather than infrastructure failures.
P4. Preflight output records public reviewer evidence including available reviewers, missing reviewers, reviewer results, failure classification, transcript hashes, and output hashes.
P5. Report-only invariants remain false and hidden oracle material remains excluded from reviewer packets and public reports.

## Implementation Decisions

The seam is the SWE-bench replay runner, because it already receives public reviewer packets and panel outcomes before oracle execution. A small preflight builder will summarize normalized panel results without invoking reviewers twice. Strict panel normalization will treat missing configured roster members as unavailable while preserving full-roster revise or deny outcomes as quality rejection.

## Testing Decisions

Tests will drive `swebench_mergeability_replay_runner` with injected configured-style panel outputs. The first tests assert unconfigured, missing-reviewer, accepting full-roster, and quality-reject full-roster behavior through emitted reports and S_full rows. Existing oracle isolation tests continue to protect hidden fields from reviewer packet content.

## Out of Scope

This slice does not run live Cursor or Codex reviewers, does not prove reviewer quality, and does not make powered improvement claims. It does not change official oracle semantics from the previous slice or populate produced baseline receipts for diagnostic comparisons.
