# Implementation Plan: PROGRAM-001

## Sequence

1. Publish the Harness v1 charter, architecture, roadmap, claim registry,
   forbidden claims, and legacy-artifact map under `docs/program/harness-v1/`.
2. Implement `ClaimGate` as the only authority that derives the maximum claim
   level and the two improvement-authority flags.
3. Resolve every evidence reference to pinned bytes and validate hashes before
   raising a claim level.
4. Recompute causal evidence from assignments, task rows, grade lineage, and
   analysis artifacts rather than trusting self-declared booleans.
5. Route report producers through the gate and reject manual or nested
   assignments of gate-owned fields.
6. Validate registered forbidden claims in structured fields and free text.

## Traceability

- P1-P2 -> `tests/test_claim_gate.py`
- P3-P5 -> `tests/test_program_001_claim_gate.py`
- Program-pack/runtime parity ->
  `test_program_pack_matches_runtime_claim_registry`

## Exit Boundary

PROGRAM-001 is complete when focused and repository-wide tests pass. It does
not by itself establish L2-L6 evidence or any outcome-improvement claim.
