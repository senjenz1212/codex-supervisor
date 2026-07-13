# PRD Grill Findings: INTEGRITY-A

### Finding 1: A conflict event that rolls back with the exception is not evidence

Status: resolved

Risk: Raising inside the terminal transaction could undo the discrepancy event and leave the conflict invisible.

Resolution: Commit the discrepancy event first, then raise a fail-closed error while preserving the original job row.

### Finding 2: An audit table without legacy backfill is not migration-safe

Status: resolved

Risk: Existing P11 fields would remain only in the mutable projection, so the projection could not be rebuilt from immutable history.

Resolution: Forward migration 11 creates the revision table and backfills non-empty legacy audit state with its original computed time.

### Finding 3: Checking only the final overlay file misses parent-directory escapes

Status: resolved

Risk: A safe-looking relative target can traverse a symlinked `.supervisor` or rollback directory.

Resolution: Validate every component and open each directory relative to the real repository root with no-follow flags.

### Finding 4: Securing approval but not rollback leaves the same write primitive exposed

Status: resolved

Risk: A crafted rollback pointer or replaced backup path could bypass approval-time checks.

Resolution: Apply repository-local validation to backup reads, approval writes, rollback writes, and failed-approval restoration.
