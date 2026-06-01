# PRD Grill Findings: Durable Substrate S3a

### Finding 1: Ledger Authority Must Be Explicit

Status: resolved

The PRD originally risks treating `result.json` and the ledger as peer sources.
That would preserve ambiguity during reconnect. The PRD now states that the
ledger wins whenever both exist, and the result file is only a compatibility
cache.

### Finding 2: Atomicity Needs A Public State Boundary

Status: resolved

Writing status through the existing generic `update_dual_agent_workflow_job`
could leave terminal status without a terminal outcome if callers forget the
second write. The PRD now requires a single state helper and a state-level test
for terminal status plus outcome in one transaction.

### Finding 3: Old Jobs Need Legacy Fallback

Status: resolved

Existing jobs and test fixtures can have `result.json` but no terminal ledger
columns. The PRD now requires file fallback only when ledger outcome is absent,
and allows backfill so future polls become ledger-first.

### Finding 4: S3a Must Not Become S3b

Status: resolved

The feature could sprawl into full event-sourcing. The PRD now scopes the change
strictly to detached workflow job terminal outcomes and names verdicts, actions,
hook requests, and gate projections as unchanged.

### Finding 5: Discrepancy Handling Must Be Auditable

Status: resolved

If the cache differs from the ledger, silently ignoring either source makes
operator debugging harder. The PRD now requires a typed discrepancy event while
still returning the ledger outcome.

### Finding 6: Worker-Side Write Path Must Close The Unpolled Window

Status: resolved

Claude's PRD review identified that poll-only backfill cannot recover a result
file deleted before first poll. The PRD now requires the detached CLI worker to
receive `job_id`, open the configured ledger, and record terminal status plus
outcome at completion.

### Finding 7: Discrepancy Equality Must Be Canonical

Status: resolved

A naive raw-file comparison can create noisy mismatch events. The PRD now pins
ledger/cache comparison to the parsed, redacted, sorted-key workflow result
object and excludes poll wrapper metadata.
