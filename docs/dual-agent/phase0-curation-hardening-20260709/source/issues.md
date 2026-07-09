# Phase 0 Curation Hardening Issues

## Slice 1: Atomic Final Artifacts

Priority: P0

Estimate: S

PRD promise: P1, P7

Scope: Route every driver-final JSON artifact through a same-filesystem tmp-then-rename write helper. The slice is verifiable by injecting a failure after the temp write and before rename.

Acceptance criteria:

- [ ] Mid-final-write failure leaves the prior complete artifact intact or no final artifact at all.
- [ ] No final artifact is left zero-byte or partial JSON after injected failure.
- [ ] Existing batch-driver manifest and authority flag tests remain green.

Blocked by: None.

## Slice 2: Per-Instance Checkpoint Receipts

Priority: P0

Estimate: M

PRD promise: P2, P4

Scope: Add atomic checkpoint receipts under the run root after each dry-oracle instance, including instance id, dry-oracle result, disk_free_gb, image-cache telemetry, timestamp, and payload hash. Checkpoint write failure produces a blocked-execution halt receipt.

Acceptance criteria:

- [ ] Each completed dry-oracle instance writes a hash-verifiable checkpoint receipt.
- [ ] ENOSPC or injected checkpoint write failure writes a clean halt receipt and exits nonzero.
- [ ] The driver does not promote a final roster as success after checkpoint write failure.

Blocked by: Slice 1.

## Slice 3: Resume From Verified Checkpoints

Priority: P0

Estimate: M

PRD promise: P3

Scope: Load existing checkpoint receipts at startup, verify hashes, skip completed intact instances, rerun tampered or invalid receipts, and report resume provenance in the roster.

Acceptance criteria:

- [ ] Restart skips intact completed checkpoint receipts and reports `resumed_from_checkpoint_count`.
- [ ] Tampered checkpoint receipts are not trusted and the affected instance reruns.
- [ ] Roster provenance reports rerun and invalid checkpoint counts.

Blocked by: Slice 2.

## Slice 4: Disk Floor And Prune Telemetry

Priority: P0

Estimate: M

PRD promise: P4, P5

Scope: Add a configurable disk floor with default `disk_floor_gb=15`, check it before each dry-oracle instance, and extend Docker prune receipts with image-level command defaults and image-cache reclaimed-byte telemetry.

Acceptance criteria:

- [ ] Below-floor disk telemetry writes a halt receipt with `reason=disk_floor_reached` and exits nonzero.
- [ ] Above-floor disk telemetry allows curation to proceed.
- [ ] Default prune command is image-level and a container-only default fails the default-config regression test.
- [ ] Prune receipts record before/after image-cache size and reclaimed bytes when measurable.

Blocked by: Slice 2.

## Slice 5: Append-Only Prereg Amendment

Priority: P0

Estimate: S

PRD promise: P6, P7

Scope: Add the append-only preregistration amendment with actual old-to-new SHA-256 mappings, halt receipt link, unchanged benchmark core, and unchanged authority flags. Update runner-pin references only if touched.

Acceptance criteria:

- [ ] Original preregistration file remains byte-identical after the slice.
- [ ] Amendment old/new SHA-256s match the actual batch driver and any touched pinned script.
- [ ] Amendment restates unchanged input pool hash, phase gates, statistical floors, solver pins, and authority flags.
- [ ] Report-only authority remains unchanged.

Blocked by: Slices 1, 2, 3, and 4.
