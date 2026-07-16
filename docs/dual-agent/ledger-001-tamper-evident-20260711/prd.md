# PRD: LEDGER-001 Tamper-Evident Evidence Ledger

## Problem

An append-oriented event table is not evidence integrity. A privileged writer
can edit, delete, reorder, fork, or truncate rows unless every event is bound
to its predecessor and an independently trusted checkpoint makes rollback
detectable.

## Goal

Make every authoritative event and artifact manifest content-addressed,
hash-chained, immutable in SQLite and PostgreSQL, and verifiable against a
signed checkpoint whose trusted identity is stored outside the ledger's
rollback domain.

## Promise Contracts

### P1: Event content is canonical and chained

- Public boundaries: `State.write_event`, `verify_event_chain`, and PostgreSQL
  equivalents.
- Every event records sequence, previous-event hash, event hash, canonical
  payload hash, artifact-manifest hash, and genesis kind.
- Editing, deleting, inserting, reordering, or forking a committed prefix
  fails verification.

### P2: Authoritative rows are immutable

- SQLite and PostgreSQL reject direct UPDATE and DELETE on ledger events.
- Projections may be rebuilt; they are never the source of truth.

### P3: Artifacts are content-addressed and symlink-safe

- Public boundary: `EvidenceArtifactStore`.
- Artifact bytes and manifests are immutable, no-follow writes rooted inside
  the configured store.

### P4: Checkpoints are signed and externally pinned

- Public boundaries: `LedgerCheckpointStore`,
  `TrustedCheckpointPinStore`, and authoritative verification.
- A checkpoint binds run ID, event count, head ID/hash, creation time, signer,
  and external anchor reference.
- Release-grade verification fails closed without a trusted latest checkpoint.
- A local sibling filesystem pin is diagnostic only; it is not sufficient
  against whole-host rollback.

### P5: Projections rebuild deterministically

- Rebuilding from the verified ledger produces byte-equivalent projections.
- Projection mutation cannot change authoritative history.

## Non-goals

- Claiming a local HMAC fixture is an independent production signer.
- Claiming an external checkpoint service is deployed when only the interface
  and hermetic tests exist.
- Treating hash chaining alone as proof that the tail is complete.
