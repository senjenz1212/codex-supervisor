# Implementation Plan: TRACE-001

## Scope Note

Planning only; this task does not own or modify graph source/tests.

## Sequence

1. Normalize logical IDs at `TraceIdentity`; retain explicit namespaces.
2. Canonically hash node revisions and validate UUID-compatible instances.
3. Enforce typed node/edge compatibility while retaining PROV kind mappings.
4. Implement deterministic promotion-path traversal and closure findings.
5. Authenticate waivers over exact rule, node revision, signer, issue time, and
   expiry; require an explicit aware `now`.
6. Adapt closure results into the planning validator.
7. Add durable serialization/storage and producer adapters for requirements,
   tests, assignments, runs, artifacts, grade revisions, analyses, decisions,
   policies, deployments, and promotions.
8. Join graph artifact references to the tamper-evident ledger.

## Integration Order

PROGRAM-001 terminology precedes identity publication. LEDGER-001 and
GRADE-001 provide immutable evidence inputs. EXP-001/TRACER-001 provide the
first real end-to-end graph. Promotion remains blocked until closure passes.

## Verification Gates

- Unit closure suite green.
- Persistence round-trip green.
- Planning validation green for a closed graph and red for every broken link.
- One real tracer produces a queryable objective-to-grade path before pilot.

## Stop Conditions

Block planning/promotion when any required link or pin is missing, a waiver is
invalid/expired, graph identity is ambiguous, or ledger and graph hashes do not
agree.
