# Issues: LEDGER-001

## T1: Canonical event chain

- Promise: P1
- Acceptance:
  - Event N commits the exact hash of event N-1.
  - Payload and artifact-manifest hashes are recomputed on read.
  - Edit, reorder, insertion, deletion, and fork probes fail.

## T2: Backend immutability

- Promises: P1, P2
- Acceptance:
  - SQLite UPDATE/DELETE triggers reject mutation.
  - PostgreSQL trigger/function rejects the same operations.
  - Schema migration and conformance tests cover both.

## T3: Content-addressed artifacts

- Promise: P3
- Acceptance:
  - Writes are append-only, rooted, and no-follow.
  - Existing identical content is idempotent.
  - Existing different content or symlink traversal blocks.

## T4: Signed checkpoints and independent pins

- Promise: P4
- Acceptance:
  - Checkpoint signature and identity are verified before persistence.
  - Trusted latest pin rejects rollback and forks.
  - Authoritative verification fails closed when signer, checkpoint, or trusted
    pin is absent.
  - Workflow terminal boundaries and a bounded event interval emit checkpoints
    when an operational authority is configured.

## T5: Deterministic projection rebuild

- Promise: P5
- Acceptance:
  - Projection bytes rebuild identically from verified events.
  - Direct projection damage does not alter ledger verification.
