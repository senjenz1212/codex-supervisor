## Problem Statement

The official replay adapter can evaluate prepared prediction artifacts, but it still cannot create matched baseline and supervisor candidates from the same public SWE-bench inputs. Operators need a budget-gated live path that generates both arms without exposing hidden oracle fields, records cost and prompt evidence, and then delegates scoring to the official replay oracle.

## Solution

Add an official live generation runner that loads official-style records through the same opt-in loader path, materializes public worktrees, invokes baseline and supervisor generators with identical model, provider, budget, timeout, and public prompt inputs, writes prediction JSONL artifacts, and evaluates those artifacts through the official replay adapter. The report remains calibration-only and cannot mutate policy.

## User Stories

1. As an evaluator, I want live generation to refuse without explicit allow_live, so accidental benchmark spending cannot occur.
2. As an operator, I want max_budget_usd to be required and enforced, so overruns become unavailable evidence rather than accepted results.
3. As a reviewer, I want generators and panel reviewers to see only public issue and repository material, so oracle fields cannot couple decisions.
4. As a benchmark maintainer, I want generated candidate hashes, prompt hashes, model labels, provider labels, wall-clock, token usage, and cost recorded, so replay evidence is auditable.
5. As the auto-improvement owner, I want the output to stay report-only, so one live calibration run cannot become an applyable policy proposal.

## PRD Promise Contracts

P1. Official live generation refuses before loaders or generators run unless allow_live is true and max_budget_usd is positive.
P2. Baseline and supervisor generator inputs are public-only, matched on model, provider, budget, timeout, prompt hash, and official instance data.
P3. Generated patches are persisted as prediction artifacts and evaluated by the official replay adapter only after decisions freeze.
P4. Budget overruns and missing generator outputs mark the live report unavailable, never accepted or policy-mutating.
P5. The final official live report records cost, wall-clock, token usage, candidate hashes, evaluator hashes, and preserves all report-only invariants.

## Implementation Decisions

The main interface will be a new official live runner in the SWE-bench mergeability module. It will reuse existing live generation normalization helpers and the official replay adapter rather than duplicating FAR, TAR, reviewer-panel, or oracle isolation logic. The generators remain injectable callables for tests and CLI adapters, while the optional dataset dependency stays lazy and operator-gated.

## Testing Decisions

The first tests will exercise the public official live runner, not helper-only seams. Tests will verify refusal before side effects, matched generator configuration, public-only generator inputs, prediction artifact creation, official replay delegation, unavailable budget behavior, and report-only invariants. Existing replay, official replay, and local live tests must remain green.

## Out of Scope

This slice does not claim powered improvement, does not run a large official benchmark in CI, does not add maintainer-grade mergeability rubrics, and does not allow automatic policy promotion. It also does not require a concrete cloud provider implementation beyond injectable generators and the existing command-generator pattern.
