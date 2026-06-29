# TDD Plan

## Issue 1: Same-Pool Roster Diagnostic Report

RED 1: Public-boundary test calls `run_mergeability_reviewer_roster_diagnostic` with deterministic reviewer receipts and asserts the report has S_probe, S_probe plus Codex, S_probe plus cross-family text-only reviewer, and S_probe plus full-panel arms sharing one candidate-pool hash.

GREEN 1: Add the diagnostic runner, row construction, arm summaries, and artifact export.

RED 2: Public-boundary test supplies a reviewer receipt for an unknown candidate and expects `MergeabilityBenchError`.

GREEN 2: Validate reviewer receipt candidate ids against the evaluated pool.

## Issue 2: Oracle-Grounded Reviewer Independence Analysis

RED 1: Public-boundary test supplies saturated zero-error reviewer decisions and asserts `effective_vote_estimate.status == "unavailable"` with a zero-error reason.

GREEN 1: Add reviewer oracle-error overlap and effective-vote summary.

RED 2: Public-boundary test supplies reviewer disagreement without oracle errors and asserts roster selection remains blocked.

GREEN 2: Make the guard require oracle-grounded reviewer errors, not disagreement alone.

## Issue 3: Roster Selection Authority Guard

RED 1: Public-boundary test supplies fixture-only evidence with reviewer errors and asserts roster selection remains blocked.

GREEN 1: Add evidence-scope checks to the diagnostic guard.

RED 2: Public-boundary test supplies disagreement-enriched evidence with oracle-grounded errors and asserts the guard can mark roster-selection evidence available while report-only invariants remain false.

GREEN 2: Allow authority only for real/disagreement-enriched evidence with reviewer oracle errors and no self-preference blocker.

## TDD Grill Findings

Resolved: The first RED tests call the public diagnostic runner, not helper-only methods.

Resolved: The plan keeps oracle labels post-decision and uses them only for aggregate scoring.

Resolved: Report-only invariants are tested on the positive authority case to avoid promotion creep.
