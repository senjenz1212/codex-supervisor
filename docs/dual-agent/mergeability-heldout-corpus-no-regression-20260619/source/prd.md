## Problem Statement

The mergeability measurement loop can now distinguish the public candidate review arm from the full supervisor gate, but the current corpus is still too small and too narrow to support a useful calibration claim. A single calculator fixture with authored controls can prove plumbing and oracle isolation, yet it cannot show whether the full gate resists varied false accepts across task classes or whether a new harness change simply fixes one case while breaking another. Operators need held-out, replayable, non-applyable calibration evidence that is strong enough to guide later live generation work without becoming a policy proposal.

## Solution

Add a held-out mergeability corpus layer with task-class metadata, control coverage checks, and a no-regression status in the paired report. The public boundary remains `run_paired_acceptance_pilot`, so callers receive one report containing candidate-pool hashes, full-gate arm metrics, held-out task coverage, no-regression findings, and report-only authority flags. The corpus validator must reject shallow fixture packs that lack both positive and negative controls per task class, and the paired report must surface when an otherwise successful candidate regresses a previously passing behavior.

## User Stories

1. As an operator evaluating supervisor improvements, I want held-out task classes in the mergeability corpus, so that calibration is not a one-fixture coincidence.
2. As an AutoResearch reviewer, I want no-regression findings in the report, so that a candidate cannot look good by trading one passing behavior for another broken behavior.
3. As a gate designer, I want held-out reports to remain non-applyable, so that fixture-scale calibration cannot mutate policy or advance gates.
4. As a future live-generation experiment owner, I want task-class and split metadata recorded in the manifest, so that later powered experiments can stratify results honestly.
5. As a skeptical maintainer, I want public-boundary tests to exercise the paired report rather than helper-only internals, so that the evidence matches the surface operators read.

## PRD Promise Contracts

P1. The paired acceptance report records held-out split and task_class coverage for every included mergeability task without exposing hidden oracle files or labels.
P2. Corpus validation rejects a held-out task class unless it contains at least one passing control and one failing control.
P3. The paired acceptance report records no-regression status and catches a candidate that breaks previously passing behavior while appearing to improve another path.
P4. No-regression and held-out coverage are observational calibration evidence only, and they cannot create applyable policy proposals or flip report-only authority flags.
P5. The first implementation tests exercise `run_paired_acceptance_pilot` or `validate_mergeability_corpus` before any helper-level checks.

## Implementation Decisions

Use the existing mergeability bench module as the deep module and extend its task/candidate payloads with optional split, task_class, and no-regression metadata. Add compact fixture tasks that reuse the same deterministic local pytest execution model instead of introducing external services. Keep no-regression computation inside report construction so the exported report, JSONL rows, and manifest agree. Preserve backward compatibility for existing fixtures by defaulting missing split to held_out and missing task_class to the task id.

## Testing Decisions

Tests start at `run_paired_acceptance_pilot` and `validate_mergeability_corpus` because those are the public interfaces consumed by AutoResearch and operator reports. The first RED checks held-out task-class coverage in the paired report, followed by validation failure for incomplete task-class controls, then a no-regression regression-catcher case, then report-only authority guardrails. Helper behavior can be inspected only after these boundary tests exist and fail for the current implementation.

## Out of Scope

This slice does not run live agent generation, does not claim production false-accept reduction, does not approximate real maintainer mergeability, and does not tune reviewer prompts or model rosters. It also does not make calibration reports applyable, approve policy overlays, or alter the full-gate reviewer-panel semantics landed in the previous slice.
