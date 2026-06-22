## Problem Statement

The mergeability evidence chain can already replay local SWE-bench-shaped bundles, but that path still depends on hand-built manifests rather than official dataset records. Operators need an opt-in replay adapter that can ingest official-style SWE-bench rows, materialize public repository worktrees at the recorded base commit, keep hidden oracle fields out of public decisions, and still produce only calibration evidence. Without that adapter, a reported FAR or TAR number can be useful plumbing evidence, but it cannot honestly represent official SWE-bench replay behavior.

## Solution

Add an official SWE-bench replay adapter above the existing mergeability replay seam. The adapter loads official-style dataset records only after explicit operator opt-in or injected test loader, resolves candidate model patches from replay predictions, materializes public bundles at base_commit through a narrow materializer seam, freezes baseline, S_probe, and S_full decisions, and then runs a deterministic oracle adapter that is labeled as Docker or equivalent. The adapter must delegate final FAR, TAR, FRR, packet isolation, reviewer-panel handling, and report-only invariants to the existing replay and bridge machinery.

## User Stories

1. As an operator, I want official-style SWE-bench replay to refuse accidental dataset fetches, so that network and benchmark runs stay intentional.
2. As an evaluator, I want patch, test_patch, FAIL_TO_PASS, and PASS_TO_PASS hidden until after decisions freeze, so that supervisor acceptance is not oracle-coupled.
3. As a reviewer, I want public packets built from issue, repo, base commit, and candidate patch hashes only, so that my decision uses the same evidence a real gate would see.
4. As a benchmark maintainer, I want the report to label Docker versus equivalent oracle adapters, so that downstream readers do not confuse a local fake with official infrastructure.
5. As the auto-improvement loop owner, I want metric_applyable and improvement_claim_allowed to remain false, so that official replay plumbing cannot mutate policy.

## PRD Promise Contracts

P1. Official replay refuses to load dataset-backed records unless explicit opt-in is present or a deterministic test loader is injected.
P2. Hidden oracle material from official records never appears in public packets, reviewer packets, generator inputs, or frozen decision rows.
P3. Baseline, S_probe, and S_full decisions are frozen on disk before any official-style oracle adapter receives hidden FAIL_TO_PASS or PASS_TO_PASS data.
P4. The replay report labels the oracle adapter kind and keeps metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced false.

## Implementation Decisions

The new module interface will be a deep adapter function that accepts dataset configuration, a predictions JSONL file, and injectable loader, materializer, and oracle callables. The default loader may import datasets lazily, but tests will use injected records to avoid network dependence. The materializer seam writes public bundles that exclude hidden paths, while the oracle seam receives hidden official fields only after freeze. The adapter will call the existing replay runner rather than duplicate FAR/TAR aggregation.

## Testing Decisions

Tests must use the public official replay function and CLI refusal path rather than private helper-only assertions. The first RED checks accidental official replay is blocked without opt-in. Follow-up tests verify hidden official fields are excluded from public and reviewer packets, freeze files predate oracle outputs, oracle adapter labels are persisted, and report-only invariants remain false. Unit helpers may be inspected only after the public behavior is pinned.

## Out of Scope

This slice does not run live model generation, publish policy proposals, claim powered improvement, require real Docker in CI, or fetch a large benchmark during tests. It does not remove the existing local replay path or the live budget-gated runner. It only creates the official-style replay bridge needed before powered live evaluation can be trusted.
