## Problem Statement

Mergeability measurement still treats the `single_agent_baseline` arm as a metadata-derived acceptance default in powered factorial reports. That default is useful for old fixture calibration, but it is not evidence that a real baseline agent produced, tested, and accepted a candidate under the same task conditions. Operators need the measurement path to distinguish a legacy metadata comparison from a replayable produced-baseline comparison before any FAR/TAR result can be trusted.

## Solution

Introduce an explicit produced-baseline decision artifact for powered mergeability evaluation. The factorial measurement path must consume replayable baseline decision rows, candidate artifact hashes, producer metadata, and unavailable states instead of silently falling back to `_baseline_accepts(candidate)`. When a baseline producer row is missing or malformed, the baseline arm becomes unavailable with an audit flag rather than accepted by default. Existing fixture calibration may keep its legacy metadata baseline, but real evaluation surfaces must label it honestly and avoid using it as the single-agent baseline.

## User Stories

1. As an operator, I want the baseline arm to come from replayable produced decisions, so that FAR/TAR comparisons are not inflated by metadata accept-all behavior.
2. As an evaluator, I want missing baseline artifacts to make the comparison unavailable, so that absent evidence cannot masquerade as a successful baseline run.
3. As a reviewer, I want candidate artifact hashes and producer metadata recorded with baseline decisions, so that I can replay whether the same candidate pool was compared across arms.
4. As a supervisor maintainer, I want legacy fixture calibration preserved but clearly labeled, so that old smoke tests keep working without being confused for real-world evidence.

## PRD Promise Contracts

P1. Produced baseline requirement: Powered mergeability evaluation reads `single_agent_baseline` decisions from explicit replayable baseline artifacts and does not default missing decisions to `_baseline_accepts(candidate)`.

P2. Fail-closed baseline availability: Missing, malformed, mismatched, or hash-inconsistent baseline rows mark the baseline arm unavailable and add a baseline evidence gaming flag rather than counting as acceptances.

P3. Replayable baseline evidence: Reports export baseline producer metadata, candidate artifact hashes, decision sources, and stable artifact hashes sufficient to reconstruct which baseline decision applied to each candidate.

P4. Honest calibration labeling: Legacy metadata accept-all behavior remains allowed only for explicitly labeled fixture calibration paths and cannot be reported as a real `single_agent_baseline` improvement claim.

## Implementation Decisions

The primary seam is `run_powered_factorial_mergeability_evaluation`, because that is where the `single_agent_baseline` arm is compared with same-model, reviewer, runtime-evidence, full-stack, and oracle ceiling arms. Add a compact baseline-decision normalization module or helper behind that interface instead of spreading fallback logic across report construction. The artifact shape should match existing arm decision mappings where possible, but add producer identity, prompt hash, candidate artifact hash, model, provider, budget, and unavailable reason fields for auditability. SWE-bench bridge and live generation code should not be moved in this slice.

## Testing Decisions

Tests must begin at the powered factorial public boundary and verify observable report behavior. The first RED test should omit `single_agent_baseline` decisions and assert the current implementation incorrectly treats metadata accepted candidates as the baseline. Follow-up tests should supply explicit baseline artifacts, corrupt candidate hashes, and legacy calibration inputs to prove the new path accepts real replay evidence, fails closed on mismatches, and leaves report-only invariants intact. Helper tests may cover normalization only after the boundary tests exist.

## Out of Scope

This slice does not run live SWE-bench, generate candidates, replace reviewer-panel aggregation, alter oracle grading, mutate policy, or claim supervisor improvement. It only makes the baseline arm honest and replayable for powered mergeability measurement so later live evaluation has a trustworthy comparison point.
