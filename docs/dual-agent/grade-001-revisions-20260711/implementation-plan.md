# Implementation Plan: GRADE-001

## Owned Files

- `supervisor/grade_revisions.py`
- `tests/test_grade_revisions.py`
- `docs/dual-agent/grade-001-revisions-20260711/**`

## Design

- Keep the module independent of `State` and trace-graph storage.
- Reuse `Grade` and `FrozenTaskResult`; map `Grade.verifier_hash` to the pinned
  verifier implementation hash and require a separate config hash.
- Canonicalize JSON evidence, detach it from caller-owned objects, and hash the
  complete immutable revision payload.
- Use one immediate SQLite transaction for revision plus supersession
  invalidation.
- Enforce a single root and a single child per revision in both code and schema.
- Recompute revision and invalidation hashes on every read.
- Validate decisions from exact citation and invalidation hashes.

## Verification

- Run only `tests/test_grade_revisions.py`.
- Run a syntax compile for the owned Python files.
- Do not run the full repository suite and do not commit.

