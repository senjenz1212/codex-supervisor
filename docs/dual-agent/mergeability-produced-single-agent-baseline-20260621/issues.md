## Issue 1: Add Produced Single-Agent Baseline Decisions To Paired Mergeability Calibration

## What to build

Add a replayable `single_agent_baseline` arm to the paired mergeability acceptance report while preserving the legacy metadata accept-all baseline as explicit calibration metadata. The slice should validate baseline decision receipts, summarize FAR and TAR separately, and refuse matched-TAR comparison when produced evidence is unavailable.

## PRD Promise

Claims P1, P2, P3, and P4 from the PRD. The public boundary is `run_paired_acceptance_pilot`; the chosen seam is the report returned by that function and the exported paired report artifact.

## Acceptance criteria

- [ ] Missing produced baseline decisions make `single_agent_baseline` unavailable, not accepted.
- [ ] Replayed produced decisions populate FAR and TAR separately from metadata accept-all.
- [ ] Matched-TAR comparison refuses unavailable produced baseline evidence.
- [ ] Legacy metadata baseline remains labeled as calibration metadata.
- [ ] Report-only invariants and policy proposal derivation remain blocked.

## Blocked by

None - can start immediately.
