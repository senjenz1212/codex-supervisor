# TDD Plan: GRADE-001

## Public Boundary

Tests use only `GradeBook`, `RunEnvelopeRef`, `DecisionGradeCitation`, and the
existing `Grade`/`FrozenTaskResult` shapes. They do not inspect SQLite tables.

## Tracer Bullets

1. Append one grade and read it back by ID; mutation of source evidence cannot
   alter the stored revision.
2. Regrade the same envelope; both revisions remain ordered and an invalidation
   links the old and replacement hashes.
3. Attempt a second child from the same parent; the append fails and history
   remains linear.
4. Cite a stale grade; validation blocks until the exact invalidation hash is
   acknowledged.
5. Append a reasoned invalidation, reopen the database, and prove both grade
   and invalidation history survive.
6. Attempt to append a grade without verifier hashes; persistence rejects it.
7. Fail terminal persistence after a passing grade is appended, then fail both
   normal regrade and invalidation. The emergency quarantine is immutable,
   survives reopen, and the original passing citation cannot validate.
