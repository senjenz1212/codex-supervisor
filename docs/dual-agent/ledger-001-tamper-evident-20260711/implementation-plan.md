# Implementation Plan: LEDGER-001

## Sequence

1. Add chain and canonical-hash columns through idempotent SQLite/PostgreSQL
   migrations.
2. Centralize canonical event hashing and verify every read.
3. Install backend triggers that reject UPDATE/DELETE.
4. Add a no-follow content-addressed artifact store and manifest binding.
5. Add signed append-only checkpoint storage and a trusted-pin protocol.
6. Separate structural prefix verification from authoritative tail
   verification.
7. Wire terminal and bounded-interval checkpoint emission behind an explicit
   operational authority; fail closed for runs marked authoritative.
8. Rebuild projections from the verified stream and compare canonical output.

## Trust Boundary

- Signer key material is not stored in an event, checkpoint, or report.
- Trusted pins must live in a rollback-independent domain.
- A filesystem implementation remains available for tests and diagnostics but
  cannot authorize production claims by itself.

## Stop Conditions

Block authoritative evidence if any chain link, artifact hash, signature,
trusted pin, backend immutability guard, or projection rebuild differs.
