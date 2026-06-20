# SWE-bench Mergeability Executable Runner PRD

## Problem Statement

The SWE-bench mergeability bridge can assemble a FAR/TAR report from supplied decisions, but it does not yet prove that the decisions were produced by real execution. Operators need a fixture-first executable path that applies a candidate patch, runs public probes, freezes arm decisions, and only then reads deterministic oracle outcomes. Without that producer path, the bridge remains useful scaffolding but cannot support trustworthy benchmark evidence.

## Solution

Build a small executable runner around the existing SWE-bench mergeability report interface. The runner will prepare a public worktree from a local SWE-like fixture, apply candidate patches, execute public static patch probes and public lint/build commands, build sanitized reviewer packets, optionally call an injected reviewer panel, freeze decisions to disk, and then run local hidden oracle commands. The emitted report stays report-only and explicitly refuses policy mutation or improvement claims.

## User Stories

1. As a supervisor operator, I want a fixture runner to produce decisions before oracle grading, so that FAR/TAR evidence is not assembled from hand-supplied labels.
2. As a benchmark maintainer, I want public commands to run against an isolated public worktree, so that hidden oracle files cannot influence probe or reviewer decisions.
3. As a reviewer-panel integrator, I want missing panel infrastructure to make S_full unavailable, so that unavailable review never becomes an implicit accept.
4. As an AutoResearch evaluator author, I want a real report artifact with decision and oracle hashes, so that later live benchmark work can reuse the same seam.

## PRD Promise Contracts

P1. The runner freezes public arm decisions before oracle material is read, using the public boundary `swebench_mergeability_fixture_runner` and the report seam `swebench_pro_mergeability_bridge_report`.
P2. The public probe substrate executes real candidate patch application and public lint/build commands, while hidden oracle commands and protected files remain unavailable before the freeze.
P3. Reviewer panel unavailability makes S_full explicitly unavailable with `reviewer_panel_unavailable`; it must never count as accepted, rejected by oracle, or imputed from S_probe.
P4. The emitted report remains report-only with `metric_applyable=false`, `improvement_claim_allowed=false`, `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

## Implementation Decisions

The primary module should be a deep runner interface rather than a second report assembler. The existing bridge stays responsible for report construction, while the new runner owns producer work: fixture materialization, public worktree preparation, patch application, public command execution, reviewer packet construction, decision freeze, oracle execution, and artifact export. Dependencies such as reviewer panels and command execution must be injectable so tests can exercise the public interface without live SWE-bench, network fetches, or live model calls.

## Testing Decisions

Tests must begin at the runner public boundary and verify observable artifacts rather than private helpers. The first RED proof should run a tiny local fixture end to end and assert that public commands execute before oracle outcomes exist. Follow-up RED/GREEN cycles cover hidden-file exclusion, decision freeze ordering, patch-apply failure, reviewer unavailability, S_probe/S_full disagreement, non-empty denominators, and report-only invariants. Existing bridge and mergeability tests remain regression coverage.

## Out of Scope

This slice does not fetch live SWE-bench instances, generate live agent patches, wire the intentionally disabled live solver, run a powered benchmark, mutate policy, or claim supervisor improvement. It also does not replace the existing bridge report shape, broaden AXI or MCP behavior, change reviewer aggregation policy, or treat test-pass oracle results as human maintainer mergeability.
