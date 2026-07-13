# Implementation Plan: EXP-001

## Scope Note

Implemented for the Harness v1 treatment-identity blocker. The resulting
evidence remains hermetic fixture/L1 evidence, not an efficacy result.

## Sequence

1. Freeze the experiment ID, assignment version, blocks, model/runtime pins,
   canonical treatment descriptors and hashes, B/C compute/resource hash,
   ceilings, failure rules, and primary/secondary comparisons.
2. Derive and persist assignment before any runtime or workspace creation.
3. For each arm in assigned order, create a fresh task environment, runtime
   session, cache namespace, and lesson namespace. Bind the preregistered
   treatment hash into launch metadata before runtime start.
4. Execute retries inside the arm budget; collect one final frozen result and
   a receipt whose treatment hash and plan fingerprint match the launch.
5. Recursively scrub treatment identity, verify the frozen result, then join
   the immutable grade to the arm.
6. Persist one immutable task result; reject discrepant rewrites.
7. Freeze primary review packets/results before outcome-aware adjudication.
8. Export one task-level analysis row and feed paired B/C analysis.
9. Integrate ledger, grade revision, trace closure, and ClaimGate.

## Failure Policy

Treatment-specific failures remain zero-score ITT outcomes. A classified
pre-treatment infrastructure failure may rerun the entire A/B/C block once
under the original assignment; otherwise the block is retained as failed or
missing according to the frozen protocol.

## Exit Gates

- Kernel and analysis tests green.
- Recursive blinding and physical isolation tests green.
- TRACER-001 proves the integrated spine.
- No pilot starts before prerequisites and protocol parameters are frozen.

## Stop Conditions

Do not launch the pilot if treatment hashes are duplicate or mutable,
assignment is mutable, B/C compute/resource hashes or ceilings differ, receipt
identity does not match preregistration/launch, arm state can leak, verifier
blinding is incomplete, or any task can contribute more than one outcome per
arm.
