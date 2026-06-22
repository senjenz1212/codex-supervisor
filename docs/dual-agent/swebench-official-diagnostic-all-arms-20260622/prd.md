## Problem Statement

Operators have official SWE-bench replay smoke evidence, oracle unavailable handling, reviewer-panel preflight, and produced baseline receipts, but they still lack one compact diagnostic that proves all measurement arms can populate together. Without that report, a green official replay can hide that S_full, the produced baseline, or matched true-accept accounting is unavailable.

## Solution

Add a small official all-arms diagnostic wrapper over the existing official replay runner. The wrapper requires produced baseline decisions, S_probe, S_full, oracle ceiling, official oracle labels, and configured reviewer-panel roster evidence to be present before it reports the diagnostic as completed. It remains diagnostic-only and never creates an applyable improvement claim.

## User Stories

1. As an operator, I want one official SWE-bench diagnostic report that states whether every arm populated, so that I can distinguish a real measurement row from replay plumbing.
2. As an evaluator, I want oracle unavailable rows to suppress the diagnostic, so that infrastructure failures are not counted as oracle-bad candidate patches.
3. As a reviewer-gate owner, I want missing S_full reviewer-panel evidence to keep the diagnostic unavailable, so that panel quality is not inferred from an uninvoked panel.
4. As a baseline owner, I want missing produced baseline receipts to block matched-TAR claims, so that metadata or gold-patch smoke is not treated as single-agent behavior.
5. As a researcher, I want n_good, n_bad, FAR, TAR, FRR, and matched-TAR status in the report, so that the next scale-up decision is based on explicit denominators.

## PRD Promise Contracts

P1. The diagnostic report completes only when official replay finishes and baseline, S_probe, S_full, and oracle ceiling arms are all available.
P2. Oracle unavailable evidence, hidden field leaks, missing reviewer roster, or missing produced baseline receipts make the diagnostic unavailable instead of accepted or imputed.
P3. The report exposes n_good, n_bad, FAR/TAR/FRR, matched true-accept status, unavailable reasons, and hidden-oracle isolation evidence at the top level.
P4. The diagnostic keeps metric_applyable, improvement_claim_allowed, human_mergeability_claim_allowed, policy_mutated, and gate_advanced false for every outcome.

## Implementation Decisions

The primary seam is the official all-arms diagnostic runner, which wraps the existing official replay runner rather than duplicating dataset loading, public bundle materialization, oracle ordering, or reviewer-packet isolation. The CLI gains a narrow flag that routes official replay inputs into this wrapper. The report stores the nested replay report for audit, but top-level fields carry the availability and matched-TAR decision that an operator needs to read first.

## Testing Decisions

Tests start at the public runner and CLI-adjacent official replay boundary, not private helpers. The first proof uses a small official-shaped dataset with two oracle-good candidates and one oracle-bad candidate because matched true-accept requires at least two positives. Additional regression tests cover oracle unavailable suppression, missing S_full reviewer evidence, missing produced baseline receipts, hidden isolation proof, and report-only invariants.

## Out of Scope

This slice does not fetch a live SWE-bench dataset, generate new model patches, power a benchmark, compare human maintainer mergeability, mutate supervisor policy, or enable auto-evolution. It only proves that the official replay diagnostic can produce populated all-arm evidence on a fixed small sample before the expensive benchmark run.
