## Problem Statement

The SWE-bench mergeability path currently proves wiring on tiny fixture candidates, but it does not replay SWE-bench-shaped checked-in bundles through the operator-visible runner. The user needs a deterministic replay path that handles public instance metadata, real model patch text, protected oracle labels, reviewer-panel availability, and report-only FAR/TAR output without live fetching or live generation.

## Solution

Add a replay runner that loads a manifest of SWE-bench-shaped instances, copies each public bundle into an isolated worktree, applies model patch artifacts as patch text, runs only public probe commands before decisions freeze, invokes the configured public reviewer-panel adapter when requested, and attaches FAIL_TO_PASS/PASS_TO_PASS oracle results only after frozen decisions are written. Expose the runner through a CLI command that writes replayable report artifacts.

## User Stories

1. As an operator, I want to run a checked-in SWE-bench replay bundle from the CLI, so that I can inspect real-instance-shaped mergeability behavior without network or live solver risk.
2. As a measurement reviewer, I want public packets to exclude patch, test_patch, FAIL_TO_PASS, and PASS_TO_PASS fields, so that supervisor decisions cannot leak hidden oracle material.
3. As a benchmark maintainer, I want model_patch text to apply or fail deterministically, so that failed patch application becomes evidence instead of an exception or silent accept.
4. As a gate reviewer, I want S_probe labeled static/lint-only when no public tests exist, so that weak public substrate cannot masquerade as hidden oracle execution.
5. As an auditor, I want frozen decisions on disk before oracle labels are read, so that FAR/TAR rows preserve decision/oracle ordering.

## PRD Promise Contracts

P1. The replay runner loads checked-in SWE-bench-shaped manifests and constructs public reviewer packets that exclude all hidden oracle material and protected path contents.
P2. The runner applies real patch text or model_patch artifacts to public worktrees, records deterministic apply receipts, and freezes baseline, S_probe, and S_full decisions before oracle results are attached.
P3. The CLI exposes replay execution without live fetches or live generation, keeps report-only invariants false for policy mutation, and reports n_good/n_bad denominators without allowing improvement claims.

## Implementation Decisions

The primary seam is the SWE-bench mergeability replay runner, because it is the same interface the CLI and public-boundary tests can invoke. The implementation should reuse the existing bridge report builder and fixture runner helpers instead of creating a parallel metric shape. Patch text should be applied against copied public worktrees through a deterministic local patch mechanism, while operation-based fixtures remain supported for existing tests. Reviewer-panel integration should remain injectable and default to unavailable unless the CLI explicitly requests configured reviewers.

## Testing Decisions

The first tests must exercise the runner and CLI boundary rather than isolated helpers. The test suite should build small replay bundles in temporary directories, include protected hidden oracle files, provide model_patch text, verify frozen decision files precede oracle outputs, and assert public reviewer packets contain only hashes and public metadata. Existing fixture-runner tests must continue passing to prove backward compatibility.

## Out of Scope

This slice does not fetch live SWE-bench instances, generate new model patches, execute official Dockerized SWE-bench grading, promote any policy change, or claim measured supervisor improvement. It also does not replace the powered factorial report or solve true maintainer mergeability beyond the existing FAIL_TO_PASS/PASS_TO_PASS oracle proxy.
