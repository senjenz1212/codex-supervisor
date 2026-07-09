# Phase 0 Curation Hardening PRD

## Problem Statement

The SWE-bench Pro powered-scale Phase 0 curation run halted on the Bokken VM after the root filesystem reached 100 percent usage with roughly 3 GB free. The curation driver and watcher died before the gate could produce usable evidence: `curated-roster.json` was left as a zero-byte file, `phase0-gate-decision.json` was absent, and no model or solver spend occurred. The operator recovered space by safely pruning Docker images after confirming no active oracle containers remained.

The user needs a Phase 0 rerun that is crash-safe before solver spend resumes. The driver must preserve evidence across interruptions, halt before disk exhaustion corrupts final artifacts, resume completed dry-oracle instances instead of restarting from zero, disclose image-cache cleanup, and document infrastructure changes through an append-only preregistration amendment.

## Solution

Make the batch driver evidence-first for Phase 0. Final artifacts use same-filesystem atomic tmp-then-rename JSON writes. Dry-oracle curation writes a per-instance checkpoint receipt after each completed instance. Restarted runs verify checkpoint hashes, skip intact receipts, rerun tampered receipts, and include resume counts in roster provenance.

Before each instance the driver checks free space against a configurable disk floor, defaulting to 15 GB. When the floor is breached, it writes a blocked-execution halt receipt with reason `disk_floor_reached` and exits nonzero instead of continuing into ENOSPC. Docker pruning remains configurable, but the default path must reclaim images and receipts must record command, return code, disk telemetry, image-cache telemetry, and reclaimed bytes when measurable. The original preregistration remains byte-identical; a sibling amendment records old and new SHA-256s, the halt receipt link, the robustness reason, and unchanged authority flags and statistical gates.

## User Stories

1. As a benchmark operator, I want final Phase 0 artifacts to be written atomically, so that a crash cannot leave zero-byte evidence.
2. As a benchmark operator, I want each completed dry-oracle instance to write an independently verifiable receipt, so that late failure does not erase progress.
3. As a benchmark operator, I want reruns to skip intact checkpoint receipts, so that recovery does not redo expensive Docker/oracle work.
4. As a benchmark operator, I want corrupt checkpoint receipts rerun, so that resume cannot promote questionable evidence.
5. As a benchmark operator, I want the driver to halt below a disk floor, so that constrained VMs fail cleanly before ENOSPC.
6. As a benchmark reviewer, I want image-level pruning and reclaimed-byte telemetry, so that the failed Bokken mode is directly addressed.
7. As a benchmark reviewer, I want an append-only preregistration amendment, so that robustness fixes are defensible without rewriting the frozen preregistration.

## PRD Promise Contracts

P1. Atomic final artifacts preserve valid evidence.

- Representative action: run the batch driver with an injected failure after writing a temporary final artifact and before rename.
- Public boundary: batch driver final artifact emission.
- Allowed outcomes: prior complete artifact remains intact or no target exists yet.
- Forbidden outcomes: target artifact exists as zero bytes or partial JSON.

P2. Per-instance checkpoint receipts are written after dry-oracle completion.

- Representative action: run curation over records with a fake oracle below the existing oracle seam.
- Public boundary: `curate_roster` with checkpoint output configured.
- Allowed outcomes: completed instance receipts include instance id, dry-oracle details, `disk_free_gb`, image-cache telemetry, timestamp, and payload hash.
- Forbidden outcomes: progress exists only in memory until the final roster write.

P3. Resume trusts only verified checkpoint receipts.

- Representative action: seed one intact checkpoint and one tampered checkpoint, then rerun curation.
- Public boundary: `curate_roster` resume behavior and roster provenance.
- Allowed outcomes: intact checkpoint is reused, corrupt checkpoint is rerun, and provenance reports resumed and invalid counts.
- Forbidden outcomes: tampered checkpoints are accepted silently or intact instances rerun unnecessarily.

P4. Disk-floor breach halts cleanly with evidence.

- Representative action: configure disk telemetry below and above the floor before curation.
- Public boundary: batch-driver curation path and CLI-shaped `main` exit behavior.
- Allowed outcomes: below-floor run writes a halt receipt and exits nonzero; above-floor run proceeds.
- Forbidden outcomes: driver continues below the floor, crashes with ENOSPC, or writes a success roster after a floor breach.

P5. Image-level prune telemetry is measurable.

- Representative action: run prune helper with fake subprocess and fake Docker usage snapshots.
- Public boundary: solver-batch prune path and prune helper.
- Allowed outcomes: default command is image-level and receipts record before/after image-cache bytes plus reclaimed bytes when measurable.
- Forbidden outcomes: default command only prunes containers or prune receipts omit reclaim evidence.

P6. Preregistration amendment is append-only and hash-true.

- Representative action: compare the original preregistration SHA before and after implementation and validate the amendment against actual file hashes.
- Public boundary: repository artifact validation test.
- Allowed outcomes: original preregistration bytes are unchanged and the amendment records actual old-to-new hashes.
- Forbidden outcomes: original preregistration edited in place, stale hashes, or omitted report-only authority flags.

P7. Curation logic and benchmark authority are unchanged.

- Representative action: run existing batch-driver manifest, solver-spend, threshold, and authority tests.
- Public boundary: current batch-driver tests and manifest generation.
- Allowed outcomes: inclusion predicates, statistical floors, solver spend gate, and report-only flags remain unchanged.
- Forbidden outcomes: robustness work loosens spend gates, changes inclusion logic, or upgrades report-only outputs into authority.

## Implementation Decisions

- Keep the slice inside `scripts/swebench_pro_batch_driver.py` and `tests/test_swebench_pro_batch_driver.py`.
- Reuse the existing batch-driver public boundary instead of adding a new orchestration layer.
- Add a small atomic-write helper and route every driver-final JSON artifact through it.
- Add checkpoint receipt helpers with payload hashing, same-filesystem atomic writes, and resume metadata.
- Add disk and image telemetry as injectable callables so tests simulate ENOSPC, disk floor, and Docker usage at system boundaries.
- Keep the Docker prune command configurable while making the default image-reclaiming command explicit.
- Add an append-only amendment beside the frozen preregistration after implementation hashes are stable.

## Testing Decisions

- Tests exercise observable batch-driver behavior through `scripts.swebench_pro_batch_driver`, with fake filesystem telemetry, fake subprocess output, and fake oracle callbacks injected below the public boundary.
- ENOSPC and mid-write failures are simulated at filesystem/write boundaries, not by faking real Docker or oracle paths.
- Resume tests assert roster provenance and oracle invocation counts rather than private loop structure.
- Prune tests verify command shape and telemetry without invoking Docker.
- Amendment tests verify SHA-256s against actual repository files and prove the original preregistration bytes remain unchanged.

## Out of Scope

- Starting solver or model spend.
- Running live Docker oracle jobs on Bokken.
- Changing curation predicates, Phase 0 gates, input pool, oracle timeout, statistical floors, solver pins, or report-only authority flags.
- Editing the original preregistration file in place.
- Claiming benchmark success from the halted Bokken run.

## Further Notes

Housekeeping was completed first in commit `0cac37b0`, recording the blocked-execution halt receipt and benchmark operations lesson. This PRD covers only the implementation slice that follows that evidence commit.
