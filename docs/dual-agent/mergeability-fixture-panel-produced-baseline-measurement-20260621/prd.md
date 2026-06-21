## Problem Statement

The mergeability calibration code can run public checks, reviewer panels, and produced baseline receipts, but the operator still lacks a canonical artifact that actually fires those pieces together. The current report keeps a legacy metadata accept-all comparison in the headline, so a green-looking run can still answer the wrong question. The operator needs a persisted fixture-scale report that proves the configured reviewer panel was available, records replayable single-agent baseline receipts, and keeps the result explicitly non-applyable.

## Solution

Add a narrow fixture measurement runner around the existing paired acceptance pilot. The runner builds replayable single-agent baseline decision receipts for every fixture candidate, invokes the configured reviewer panel through the existing panel seam, persists the paired acceptance report, and marks `supervisor_vs_single_agent_baseline` as the primary comparison. It must preserve the metadata accept-all arm as a legacy calibration reference while making unavailable panels or unavailable baseline receipts visible slice failures.
Because `S_full` is a conjunction of public floor and panel acceptance, rows already rejected by the public floor are recorded as available full-gate rejects without spending reviewer budget; panel verdicts remain mandatory for public-floor accepts.

## User Stories

1. As an operator, I want one fixture measurement command or runner to produce the panel-on report, so that I can inspect a real number instead of another wiring diff.
2. As a supervisor reviewer, I want every full-gate decision to include reviewer ids, packet hashes, panel decisions, and rationale evidence, so that acceptance is auditable candidate by candidate.
3. As an evaluator, I want the produced single-agent baseline to be represented by replayable receipts, so that missing or malformed baseline evidence cannot silently become accept-all behavior.
4. As a policy owner, I want calibration outputs to remain non-applyable, so that fixture evidence cannot mutate gates or claim production improvement.

## PRD Promise Contracts

P1. The fixture measurement runner invokes `run_paired_acceptance_pilot` with `reviewer_panel_mode="configured"` and fails the slice when `supervisor_full_gate` remains unavailable.
P2. The runner supplies replayable single-agent baseline receipts for every fixture candidate and reports `supervisor_vs_single_agent_baseline` as the primary comparison.
P3. The persisted report records reviewer packet hashes and panel decisions for each candidate, plus reviewer ids and rationale evidence for every panel-invoked candidate, without hidden oracle material.
P4. The legacy metadata accept-all arm remains present but is labeled non-primary calibration evidence.
P5. The report keeps `metric_applyable=false`, `improvement_claim_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.
P6. The full-gate arm treats public-floor rejects as deterministic full-gate rejects and requires reviewer verdicts only for public-floor accepts.

## Implementation Decisions

Use `supervisor.mergeability_bench` as the deep module because it already owns candidate loading, corpus manifests, configured reviewer panels, full-gate packets, baseline receipt normalization, and paired report export. Add one small public runner for this fixture measurement rather than creating a parallel report schema. Build baseline receipts from fixture candidate metadata and candidate hashes without consulting oracle labels, and annotate the existing report with comparison metadata before exporting.

## Testing Decisions

The first tests exercise the public runner boundary, not helper internals. They verify that a configured panel is invoked, the produced baseline arm is available, the primary comparison is named and points at `single_agent_baseline`, reviewer packet and rationale fields are exported per candidate, unavailable panels fail the measurement runner, and report-only invariants remain false. Helper-level tests are allowed only after the public report behavior is covered.

## Out of Scope

This slice does not fetch official SWE-bench instances, generate live patches, enlarge the fixture corpus, change reviewer aggregation, or allow policy proposals. It does not claim production improvement, because the fixture corpus is small and authored. It also does not make metadata accept-all disappear; the old arm remains useful as a calibration reference when clearly labeled.
