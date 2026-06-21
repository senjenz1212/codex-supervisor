## Problem Statement

The SWE-bench mergeability path can replay checked-in patch artifacts, but it still cannot run a live candidate-generation pass under explicit spend controls. Operators need a safe path that only runs when live execution is intentionally enabled, keeps baseline and supervisor arms budget matched, and preserves the replay runner's oracle isolation and report-only guardrails.

## Solution

Add a budget-gated live runner that builds public-only generator inputs from SWE-bench replay bundles, invokes baseline and supervisor generator adapters with the same provider, model, timeout, and budget, writes generated model patches into a replay manifest, and delegates evaluation to the replay runner. If live execution is not explicitly enabled, budget is missing, a generator exceeds budget, or oracle material appears in public inputs, the run must be unavailable rather than accepted.

## User Stories

1. As an operator, I want live SWE-bench mergeability runs to require `allow_live` and a positive budget, so that accidental spend cannot happen from replay defaults.
2. As a measurement reviewer, I want baseline and supervisor generation arms to use the same provider, model, timeout, and budget, so that the comparison measures supervision rather than spend or model mismatch.
3. As an auditor, I want provider, model, prompt hash, cost, wall-clock, token usage, and artifact hashes recorded, so that generated candidates can be traced and replayed.
4. As a safety reviewer, I want hidden oracle material unavailable to generators and reviewers, so that live runs do not become oracle-coupled.
5. As a policy owner, I want live reports to remain non-applyable until a later powered-evidence slice, so that one run cannot mutate defaults or create an improvement claim.

## PRD Promise Contracts

P1. The live runner refuses execution unless `allow_live=true` and `max_budget_usd` is positive, and generator adapters are not invoked when either guard fails.
P2. The live runner invokes baseline and supervisor generators with matched provider, model, timeout, and budget, records generation receipts, and marks budget-overrun arms unavailable rather than accepted.
P3. The live runner passes only public bundle content and public metadata to generators and reviewers, delegates oracle evaluation to the replay runner after frozen decisions, and keeps all policy mutation and improvement-claim flags false.

## Implementation Decisions

The live interface should sit above the existing replay runner instead of duplicating oracle and FAR/TAR logic. Generator adapters produce patch text and metadata, while the implementation writes those patches into a generated replay manifest consumed by the existing replay runner. The CLI should keep replay as the default path and require explicit live flags plus generator commands for approved live execution.

## Testing Decisions

Tests must begin at the live runner and CLI boundary with fake generator adapters. They should prove refusal without live flags, refusal without budget, matched generation config, hidden oracle exclusion from generator inputs, budget-overrun unavailability, freeze-before-oracle ordering through replay output, and report-only invariants. Existing replay, bridge, and pilot tests must remain green.

## Out of Scope

This slice does not fetch SWE-bench instances from a network dataset, implement a specific commercial model provider, run the official Dockerized SWE-bench grader, perform powered significance analysis, change default policies, or promote any AutoResearch proposal from live calibration output.
