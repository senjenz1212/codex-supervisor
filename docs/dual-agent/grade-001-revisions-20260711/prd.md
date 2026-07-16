# PRD: GRADE-001 Immutable Grade Revisions

## Goal

Never overwrite a grade. Regrading the same captured run appends a hash-pinned
revision, preserves the prior revision, and records explicit invalidation
lineage. Decisions must cite the exact revision hash and may use a stale grade
only by acknowledging every invalidation hash recorded for it.

## Promise Contracts

### P1: Grade history is append-only

- Public seam: `GradeBook.append_grade`, `get_revision`, and `list_revisions`.
- A root grade pins one `RunEnvelopeRef`, verifier ID/version/config hash/
  implementation hash, score, evidence, failure classification, and flake
  classification.
- Regrading appends a new revision with `supersedes_grade_id`; it never mutates
  the prior row.

### P2: Supersession is a linear chain

- A run envelope has one root.
- Only the current chain head may be superseded.
- Two revisions cannot supersede the same grade.

### P3: Invalidation is immutable evidence

- Supersession atomically appends an invalidation referencing both immutable
  revision hashes.
- `invalidate_grade` appends a reasoned invalidation without deleting or
  rewriting the grade.

### P4: Stale decision citations fail closed

- A citation pins `grade_id` plus `revision_hash`.
- Unknown or hash-mismatched citations block.
- A stale citation blocks unless it acknowledges every recorded invalidation
  hash exactly.
- Grades without complete verifier provenance are rejected before persistence.

## Integrity Boundary

All hashes are lowercase SHA-256 content-integrity identifiers. They are not
digital signatures and make no claim about signer identity or authenticity.

## Out of Scope

- Editing `state.py` or the concurrently owned trace-graph implementation.
- Producing trace-graph nodes or edges directly; returned revision and
  invalidation records contain the hashes needed by that owner.
- A repository-wide database migration or PostgreSQL adapter.
