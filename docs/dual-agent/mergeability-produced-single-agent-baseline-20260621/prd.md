## Problem Statement

Mergeability calibration still presents fixture metadata as the baseline decision arm in the paired acceptance report. That legacy arm is useful for compatibility, but it is not replayable evidence from a produced single-agent run. Operators need the report to separate metadata accept-all behavior from an actual produced baseline artifact before comparing supervisor false-accept rates.

## Solution

The paired acceptance pilot will keep the legacy metadata baseline clearly labeled while adding a first-class `single_agent_baseline` arm. The new arm will consume replayable decision receipts keyed by candidate, validate the candidate artifact hash and receipt fields, and mark itself unavailable when evidence is absent or malformed. Calibration remains report-only.

## User Stories

1. As an operator, I want metadata accept-all labeled separately, so that I do not mistake fixture self-report fields for a real baseline.
2. As an evaluator, I want replayed single-agent decisions summarized independently, so that FAR and TAR comparisons use produced evidence when available.
3. As a reviewer, I want missing produced baseline evidence to be unavailable, so that absent artifacts cannot silently become acceptance.
4. As a maintainer, I want report-only invariants preserved, so that baseline calibration cannot mutate supervisor policy.

## PRD Promise Contracts

P1. The paired report preserves the legacy metadata baseline under an explicitly metadata-labeled arm and row fields.
P2. The paired report adds `single_agent_baseline` only from validated replayable decision receipts containing candidate identity, prompt hash, producer labels, artifact hash, decision, and unavailable status.
P3. Missing, malformed, mismatched, or unavailable baseline receipts mark `single_agent_baseline` unavailable and make matched-TAR comparisons unavailable rather than accepted or imputed.
P4. Produced-baseline calibration remains non-applyable, report-only, and unable to generate a policy proposal.

## Implementation Decisions

The public seam is `run_paired_acceptance_pilot`, because that is the report-producing interface consumed by calibration callers. The existing powered-factorial baseline resolver will be reused or generalized instead of inventing a second receipt shape. Legacy `baseline` output remains compatible, while `metadata_accept_all_baseline` and `single_agent_baseline` make the decision provenance explicit.

## Testing Decisions

Tests will start at the paired pilot report boundary and assert observable report fields, arm summaries, row evidence, matched-TAR status, and policy proposal behavior. Helper-level validation tests may be added only after the boundary proves the externally visible report semantics. Existing fixture calibration and powered factorial tests must stay green.

## Out of Scope

This slice does not generate live single-agent patches, run external providers, promote any policy, or claim supervisor improvement. It does not replace the metadata baseline everywhere; it labels that legacy calibration arm and adds replayed produced decisions where artifacts are supplied.
