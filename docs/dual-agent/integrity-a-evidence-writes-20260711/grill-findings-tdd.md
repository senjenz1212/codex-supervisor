# TDD Grill Findings: INTEGRITY-A

### Finding 1: Duplicate and conflicting completion are distinct behaviors

Status: resolved

Risk: One broad test could pass while either idempotency or fail-closed discrepancy evidence remains broken.

Resolution: Use separate public-interface tests for canonical duplicates and conflicting outcomes.

### Finding 2: Counting projection rows does not prove audit immutability

Status: resolved

Risk: The latest projection can look correct while the prior audit revision has been erased.

Resolution: Query the immutable audit interface and assert both ordered revisions plus the latest projection.

### Finding 3: New-database tests do not prove migration safety

Status: resolved

Risk: `CREATE TABLE IF NOT EXISTS` passes while existing audit evidence is never copied.

Resolution: Construct a legacy quality-trend table with P11 data, run forward migrations, and assert exact backfill.

### Finding 4: Normalization-only tests do not prove write-path enforcement

Status: resolved

Risk: Approval code could bypass the normalizer or write a backup through a separate unsafe primitive.

Resolution: Exercise approval with symlinked target and backup paths, assert no outside bytes and no approval event, then run the legitimate approval/rollback suite.
