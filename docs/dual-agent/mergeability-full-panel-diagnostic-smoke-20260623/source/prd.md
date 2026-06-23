## Problem Statement

The mergeability evaluator now has Cursor reviewer worktree isolation, but the project still lacks a fresh diagnostic artifact proving the configured full reviewer panel can run on the existing fixture corpus. Without this smoke, S_full remains a wired path rather than observed evidence, and reviewer infrastructure failures can still be mistaken for quality decisions.

## Solution

Run the existing paired acceptance fixture corpus through the configured reviewer panel and write a diagnostic report that records what actually happened. The run must keep S_probe and S_full separate, preserve oracle isolation, expose Cursor and Codex reviewer availability, and leave every policy or improvement flag disabled.

## User Stories

1. As the supervisor operator, I want a full-panel diagnostic report, so that I can tell whether configured reviewer evidence is available before building larger benchmarks.
2. As the evaluator maintainer, I want Cursor isolation diagnostics captured, so that source-worktree mutation is separated from contained reviewer scratch changes.
3. As a benchmark reviewer, I want unavailable reviewers labeled as infrastructure or configuration failures, so that missing evidence is never treated as acceptance.
4. As a research consumer, I want panel marginal output to compute or explain why it cannot compute, so that calibration evidence is not overstated.

## PRD Promise Contracts

P1. The public boundary run writes a paired acceptance diagnostic report using reviewer_panel_mode configured and the existing mergeability fixture corpus.
P2. The diagnostic report records Cursor SDK isolated-worktree evidence and does not allow cursor_modified_worktree on the source worktree to pass silently.
P3. S_full remains a conjunction of S_probe and independent panel acceptance, with missing Cursor or Codex verdicts blocking full-panel availability.
P4. Panel marginal output must be computed or explicitly labeled unavailable, not_matched, or insufficient with a concrete reason.
P5. The report keeps metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced false.

## Implementation Decisions

Use the existing run_paired_acceptance_pilot seam instead of adding a new benchmark path. The implementation may add a thin diagnostic command or helper only if the current public interface cannot persist the required report and reviewer diagnostics. Do not add a mergeability rubric, do not change gate authority, and do not let hidden oracle outcomes become reviewer-assessed blocker criteria.

## Testing Decisions

Tests must start at the public diagnostic boundary by running the configured full-panel smoke or an equivalent public helper that writes paired_acceptance_report.json. Focused assertions must cover reviewer availability, Cursor isolation diagnostics, Codex verdict presence, S_full non-imputation, panel marginal status, oracle leak rejection, and report-only invariants.

## Out of Scope

This slice does not grow the corpus, run SWE-bench live generation, add a human mergeability rubric, promote policy proposals, or claim false-accept improvement. A not_matched or unavailable panel marginal is an acceptable diagnostic outcome when the report explains the exact blocker.
