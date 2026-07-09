# TDD Grill Findings

## Scope

This grill reviewed `issues.md` and `tdd.md` against the PRD promise contracts and the repository testing guidance. Findings are resolved before implementation.

### Finding 1: First tests must exercise artifact outcomes

Status: resolved

Concern: Atomic write tests could overfit to helper names instead of proving the operator-visible artifact guarantee.

Resolution: The first two TDD cycles assert final artifact contents and zero-byte absence after injected failure. Helper structure is incidental.

### Finding 2: Checkpoint tests must not fake real oracle paths

Status: resolved

Concern: The task forbids mocked Docker/oracle in tests of real paths; a test that bypasses all curation behavior would be too shallow.

Resolution: The TDD plan uses fake oracle callbacks below the curation boundary already supported by the existing test suite, while ENOSPC and prune behavior are simulated at filesystem and subprocess boundaries.

### Finding 3: Resume must prove both skip and rerun behavior

Status: resolved

Concern: A single resume happy-path test would not protect against accepting tampered checkpoints.

Resolution: The TDD plan includes `test_resume_skips_verified_completed_checkpoints` and `test_resume_reruns_tampered_checkpoint_receipt`.

### Finding 4: Disk-floor tests must assert nonzero exit and no success promotion

Status: resolved

Concern: A disk-floor receipt alone is not enough if the driver still returns success or leaves a successful final roster.

Resolution: `test_disk_floor_breach_writes_halt_receipt_and_exits_nonzero` asserts all three observable outcomes.

### Finding 5: Amendment validation must wait for final hashes

Status: resolved

Concern: The prereg amendment cannot be honestly completed until source changes settle and actual new SHA-256s are known.

Resolution: The amendment test is sequenced last, after implementation hashes are stable, and requires actual file hash comparison.
