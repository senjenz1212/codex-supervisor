# Mergeability All-Arms Diversity Wiring

## Problem Statement

The official all-arms diagnostic report already assembles reviewer analysis rows, reviewer provenance, and generator-disjointness signals, but it does not surface the existing inter-reviewer independence metrics that the roster diagnostic emits. Operators reviewing the all-arms report therefore cannot see whether the reviewer panel behaved independently without running a separate diagnostic path over a different report shape.

## Solution

Wire the official all-arms diagnostic report to call the existing reviewer-independence helpers on the rows it already builds, then expose their results beside reviewer provenance and generator disjointness. The change is report-only: it does not alter oracle adapters, candidate generation, baseline decisions, reviewer aggregation, policy evolution, or authority flags.

## User Stories

1. As a Supervisor operator, I want the official all-arms report to include pairwise reviewer agreement, so that I can inspect panel independence in the same artifact as all-arm availability.
2. As a benchmark reviewer, I want leave-one-reviewer-out sensitivity in the all-arms report, so that I can see whether one reviewer dominates full-gate behavior.
3. As an AutoResearch gate maintainer, I want effective vote estimates in the all-arms report, so that independence evidence remains visible without promoting policy changes.
4. As a maintainer, I want empty or zero-error reviewer evidence to remain honestly unavailable, so that report consumers do not mistake absent independence evidence for a positive claim.

## PRD Promise Contracts

P1.
Public boundary: `_build_official_all_arms_diagnostic_report` return dict.
Chosen seam: call existing reviewer-independence helpers on `_official_reviewer_analysis_rows(...)` output during official all-arms report assembly.
Allowed outcomes: `inter_reviewer_agreement`, `leave_one_reviewer_out`, and `effective_vote_estimate` are emitted for the same official candidate pool as `reviewer_provenance` and `generator_disjointness`; unavailable reviewer rows or zero oracle-grounded reviewer errors keep the existing honest unavailable statuses; all authority flags remain false.
Forbidden outcomes: do not recompute or fork metric logic; do not emit computed independence claims for empty reviewer rows; do not build the autonomous benchmark-to-policy bridge; do not mutate candidates, baseline behavior, oracle adapters, reviewer aggregation, policy overlays, or default authority.

## Implementation Decisions

Use the official all-arms diagnostic assembly path as the only modified production surface. Import the existing helpers from `supervisor.mergeability_bench`, derive only the minimal adapter inputs they already require, and attach the resulting metrics in the report next to `reviewer_provenance` and `generator_disjointness`.

The official report should keep using the same candidate rows and candidate-pool hash for every surfaced independence metric. This preserves locality: the all-arms report remains the deep module interface for all diagnostic evidence, while metric behavior stays inside the existing mergeability helper implementations.

## Testing Decisions

The first RED test is `tests/test_allarms_diversity.py::test_all_arms_report_includes_independence_metrics`. It calls `_build_official_all_arms_diagnostic_report` directly because the promise is about the returned diagnostic dict. The fixture uses a two-reviewer panel with at least one oracle-grounded reviewer error so the three metrics populate, then no-reviewer and zero-error reports to prove the unavailable paths stay honest.

## Out of Scope

This slice does not change the oracle adapter, candidate generation, produced baseline receipts, S_probe, S_full aggregation, the reviewer panel itself, AutoResearch policy evolution, or any benchmark-to-`records[]` promotion bridge.

## Further Notes

No silent mocking is allowed above the return-dict boundary. Test fixtures may construct official replay-shaped dictionaries below that boundary, but the report assembly under test must be real production code.
