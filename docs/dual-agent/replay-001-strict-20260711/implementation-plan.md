# Implementation Plan: REPLAY-001

## Scope Note

This is a planning artifact. No source or test file is owned or edited by this
documentation task.

## Sequence

1. Keep `replay_versions` as the single strict compatibility boundary.
2. Preserve explicit contracts for rerun, regrade, and replay.
3. Resolve a manifest to repository, recorded commit, and optional immutable
   overlay before invoking any historical verifier.
4. Materialize a detached temporary checkout, validate overlay schema/hash and
   path safety, run the verifier there, then remove the checkout.
5. Build execution provenance from recorded events and workspace metadata;
   never infer completeness from a model alias alone.
6. Export schema versions and component hashes into the replay manifest.
7. Keep audit fixtures compatible with append-only events by supplying
   timestamps at insertion or through a supported test seam.

## Integration Gates

- INTEGRITY-A/LEDGER behavior must not make replay tests rely on event updates.
- OBS-001 must preserve raw events needed by manifest extraction.
- GRADE-001 regrades must reference the same frozen result and verifier
  revision.
- TRACE-001 evidence nodes must pin the replay manifest and run.

## Stop Conditions

Stop and report `incompatible` if the schema set, commit, overlay, component
hashes, or verifier provenance cannot be established. Do not substitute the
live checkout and do not upgrade the claim level.
