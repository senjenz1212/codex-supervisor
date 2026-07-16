# Evidence Status: LEDGER-001

## Status

**Core chain, immutability, artifact, checkpoint, and hermetic commit
components exist. Operational external anchoring is NOT RUN and remains a
release blocker.**

## Implemented Evidence Surfaces

- canonical event hash chaining in SQLite and PostgreSQL schemas;
- immutable UPDATE/DELETE guards;
- structural and authoritative verification APIs;
- symlink-safe content-addressed artifact manifests plus detached direct
  manifest signatures;
- append-only signed checkpoint storage and trusted-pin protocol;
- deterministic projection checks for the exact evidence-authoritative scope
  in `docs/program/harness-v1/projection-registry.yaml`, with exact pytest-node
  attribution proving the registered implementations executed;
- one state-level idempotency/authority claim for each evidence-commit manifest
  event, preventing duplicate publication across committer roots.

## Not Yet Proven

- No externally managed production signing key was used.
- No rollback-independent remote pin store receipt exists.
- Periodic and terminal lifecycle checkpoint wiring has not yet been validated
  against a production workflow.
- The TRACER local HMAC and sibling pin directory prove API composition only.
- No pilot, confirmation, promotion, deployment, or scale run used this
  evidence.
- Unregistered Supervisor materializations are not claimed to rebuild from the
  ledger.

## Claim Boundary

The current branch may claim local tamper-detection behavior where the focused
tests pass. It may not claim production tail completeness, externally anchored
evidence, causal improvement, portability, ROI, or auto-improvement.
