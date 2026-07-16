# PRD Grill Findings: GRADE-001

## Finding 1: A mutable latest-grade row would erase the evidence under review

Status: resolved.

Resolution: store every grade as an immutable SQLite row and expose ordered
history; no update/delete method exists and SQLite triggers reject both.

## Finding 2: `supersedes_grade_id` alone does not prevent forks

Status: resolved.

Resolution: require the superseded revision to be the current head and enforce
a unique child per grade plus a unique root per run envelope.

## Finding 3: A boolean stale acknowledgement is not auditable

Status: resolved.

Resolution: decision citations acknowledge concrete invalidation hashes. Every
recorded hash must be present and unrelated hashes are rejected.

## Finding 4: A hash must not be described as a signature

Status: resolved.

Resolution: the module and PRD explicitly describe SHA-256 values as integrity
identifiers only; no signature fields or signer claims are created.
