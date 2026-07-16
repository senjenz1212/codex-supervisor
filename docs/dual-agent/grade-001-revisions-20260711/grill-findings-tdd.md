# TDD Grill Findings: GRADE-001

## Finding 1: Checking only the latest grade does not prove history retention

Resolution: the regrade test queries ordered public history and asserts both
scores, IDs, and the supersession link.

## Finding 2: A successful second append does not prove fork prevention

Resolution: a separate test attempts a competing child from the original grade
and verifies that only two revisions remain.

## Finding 3: Stale detection without exact hashes can acknowledge the wrong fact

Resolution: tests cover an unacknowledged stale citation, a wrong revision hash,
an exact invalidation acknowledgement, and a current citation.

## Finding 4: Process-local assertions do not prove persistence

Resolution: explicit invalidation is verified after closing and reopening the
SQLite-backed `GradeBook`.
