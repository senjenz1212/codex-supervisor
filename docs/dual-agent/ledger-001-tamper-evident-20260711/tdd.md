# TDD Plan: LEDGER-001

## Public-Boundary Tracer Bullets

1. Write a multi-event run and verify its expected head.
2. Mutate payload, previous hash, event hash, sequence, and artifact manifest;
   each mutation fails.
3. Attempt UPDATE and DELETE through SQLite and PostgreSQL; both reject.
4. Persist a signed checkpoint, pin its identity, reopen stores, and verify the
   authoritative stream.
5. Present a valid local prefix behind the trusted latest checkpoint; detect
   truncation.
6. Present a competing checkpoint at the same count; detect a fork.
7. Write artifact bytes through a symlink; reject without touching the target.
8. Rebuild every covered projection twice and compare canonical bytes.
9. Complete a configured workflow run and observe a terminal checkpoint.
10. Cross the configured event interval and observe a periodic checkpoint.
11. Infer the event-hash schema from its hash preimage and apply only that
    schema's frozen redactor; accept a historical future-only secret, reject
    the same unredacted value under a future schema, and reject unknown
    schemas.

## Focused Suites

```text
uv run pytest -q \
  tests/test_state_event_ledger.py \
  tests/test_evidence_ledger_conformance.py \
  tests/test_evidence_ledger_hardening.py \
  tests/test_evidence_committer.py \
  tests/test_postgres_ledger_lane.py \
  tests/test_schema_migrations.py
```

Hermetic signing proves composition only. Operational signer and pin-store
receipts are separate required evidence.
