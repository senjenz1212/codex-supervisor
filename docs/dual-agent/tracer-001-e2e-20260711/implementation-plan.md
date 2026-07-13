# Implementation Plan: TRACER-001

## Status

Planning only. The end-to-end tracer is **not run** by this task.

## Sequence

1. Select and pin one generic and one Unity task that will never enter PILOT or
   CONFIRM.
2. Freeze runtime/model, verifier, image, network, resource, assignment, and
   arm-budget manifests.
3. Precompute the twelve task × arm × runtime coordinates.
4. For each task/runtime:
   - persist assignment;
   - materialize a fresh arm workspace/session;
   - submit/register the run;
   - normalize and join events;
   - collect and freeze the result;
   - recursively blind treatment identity;
   - invoke the hidden verifier;
   - append an immutable grade;
   - append/verify ledger evidence;
   - add trace nodes/edges and validate closure.
5. Build the ClaimGate evidence bundle and assert L2 maximum.
6. Emit one immutable report with all hashes, statuses, costs, latencies, and
   failures.

## Required Outputs

- Frozen tracer manifest and task-set hash.
- Twelve assignment/run/result records.
- Normalized event join report.
- Hidden-test leak-check report.
- Frozen result and grade hashes.
- Ledger verification report.
- Objective-to-grade trace query and closure result.
- ClaimGate report showing L3 refusal.

## Stop Conditions

Stop without claiming completion if any runtime/verifier is unavailable, an
arm is missing, a join is ambiguous, hidden/treatment data leaks, the ledger or
trace fails, or ClaimGate permits L3.
