# Mergeability Bench Calibration And Paired Acceptance Pilot PRD

## Problem Statement

Supervisor now has a deterministic mergeability bench foundation, but the corpus is still too small to support claims about verification quality. A single toy task proves the grader can run, not that the evaluator catches diverse failure modes or measures Supervisor against a baseline fairly. The next step must calibrate the benchmark itself before using it to justify policy or prompt changes.

## Solution

Expand the local held-out corpus into a manifest-backed set of task and control cases, then add a paired acceptance pilot over a fixed candidate pool. The baseline arm accepts candidates from self-reported or visible-test evidence, while the Supervisor arm accepts only candidates with runtime-native mergeability blocker receipts. The pilot exports report-only metrics and evidence artifacts without mutating policy.

## User Stories

As a Supervisor operator, I want the evaluation harness to reveal false accepts before AutoResearch proposes policy changes. As a workflow maintainer, I want every benchmark task to prove positive and negative controls so the aggregate report cannot hide a broken oracle. As a researcher, I want paired arm comparisons over identical candidates so cost, consistency, false accepts, and true accepts can be inspected directly.

## PRD Promise Contracts

P1. The corpus manifest records every mergeability task hash, candidate hash, expected control role, and calibration eligibility before aggregate reporting includes that task.

P2. Corpus calibration rejects broken or non-discriminating controls, including no-op, known-bad, test-gaming, scope-escape, and known-good regressions.

P3. The paired acceptance pilot evaluates baseline and Supervisor acceptance over the same candidate pool and reports false_accept_rate, true_accept_rate, false_reject_rate, cost, duration, and disagreements.

P4. All pilot outputs remain report-only: default_change_allowed, policy_mutated, and gate_advanced stay false, and no applyable policy proposal is created from this pilot.

## Implementation Decisions

The first implementation stays local and deterministic. It reuses `supervisor.mergeability_bench` as the public grading boundary, adds manifest and paired-report helpers around that boundary, and expands `tests/fixtures/mergeability_bench/` with calibrated candidates rather than invoking live model generation. The baseline arm intentionally represents a weaker accept policy so seeded false accepts are visible and explainable.

## Testing Decisions

The first tests exercise corpus manifest construction, calibration, and paired pilot reporting through public functions that load real fixture files. Tests must prove missing positive or negative controls are excluded or rejected, seeded bad candidates can fool baseline acceptance, Supervisor acceptance rejects them through runtime-native blocker results, and report-only invariants remain false. Existing mergeability and AutoResearch evaluator-quality tests must remain green.

## Out of Scope

This slice does not run live multi-model generation, public SWE-bench, paid FrontierCode execution, or automatic policy approval. It does not claim Supervisor improves accuracy in production. It creates a calibrated local measurement surface and a report-only paired pilot that later powered experiments can use without changing gate authority.
