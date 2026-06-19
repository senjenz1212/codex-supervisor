## Problem Statement

Supervisor AutoResearch currently has a mergeability calibration pilot that separates the bench oracle from the public-check decision, but the measured supervisor arm is still not the full supervisor gate. Operators need a measurement path that distinguishes the deterministic public evidence floor from the reviewer-panel gate, because false-accept reduction is supposed to measure the full supervised workflow rather than public tests alone.

## Solution

Add a full-gate mergeability measurement arm that keeps the existing public-check arm and layers an independent reviewer panel over the same oracle-isolated public evidence packet. The report will show both `S_probe` and `S_full`, preserve their disagreements, record reviewer packet references, and keep every metric report-only until later corpus, live-generation, and powered-evaluation slices make the estimate applyable.

## User Stories

1. As a supervisor operator, I want mergeability reports to show whether the public-check arm or the full reviewer-panel gate accepted a candidate, so that I can see which part of the workflow created each false accept.
2. As an AutoResearch maintainer, I want reviewer prompts and packets to exclude hidden oracle material, so that panel decisions cannot accidentally read the answer key they are supposed to be measured against.
3. As a workflow reviewer, I want full-gate reports to include reviewer packet references, panel decisions, and unavailable-reviewer status, so that I can replay why a measured supervisor decision was counted or marked unavailable.
4. As a policy maintainer, I want calibration reports to remain non-applyable, so that a small fixture result cannot mutate prompts, config, reviewer authority, or gate policy.

## PRD Promise Contracts

P1. The mergeability paired report will preserve the existing `supervisor_candidate_review` public-check arm and add a separately labeled `supervisor_full_gate` arm with its own FAR, TAR, FRR, and decision source.
P2. The full-gate arm will build and record an oracle-isolated reviewer packet that contains public task, candidate, runtime, and receipt evidence while excluding hidden tests, final scores, expected outcomes, protected paths, and oracle labels.
P3. The full-gate decision will use independent reviewer-panel output when available, will mark the full-gate metric unavailable when required reviewers are unavailable, and will never impute reviewer acceptance from the public-check arm.
P4. The report will preserve disagreements among baseline, public-check, full-gate, and oracle-ceiling arms, including panel marginal delta where matched true-accept is computable.
P5. The slice will keep `metric_applyable`, `improvement_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced` false for calibration output.

## Implementation Decisions

The public interface for this slice is `run_paired_acceptance_pilot`, because current tests, evaluator reports, and exported artifacts already consume that boundary. The deep module interface should remain a single report-producing function that hides reviewer packet construction, leak detection, and panel aggregation behind stable report fields. Existing reviewer-panel adapters and result normalization should be reused rather than inventing a second panel contract inside the mergeability module.

## Testing Decisions

The first tests must call `run_paired_acceptance_pilot` and assert report-level behavior through the exported arms, rows, and replayable artifacts. Unit tests below that boundary may cover reviewer packet leak detection and unavailable reviewer outcomes after the report-level test exists. Tests must inject reviewer results below the public interface so they do not call live Cursor, Codex, Claude, or external model APIs by default.

## Out of Scope

This slice does not grow the corpus, run live candidate generation, power a production improvement claim, change reviewer roster defaults, apply policy proposals, or redefine bench-oracle mergeability as maintainer mergeability. Later slices handle held-out corpus scale, budget-matched live candidates, powered factorial analysis, and human-approved policy evolution.
