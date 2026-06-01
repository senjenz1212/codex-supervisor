# Issues: Durable Substrate S3a

## Slice 1: Persist Terminal Outcome Columns

Priority: P0

Scope: Add nullable terminal outcome fields to `dual_agent_workflow_jobs` and a
forward migration for existing databases.

Acceptance Criteria:

- [ ] Existing DBs opened after the change gain terminal outcome columns.
- [ ] New DB schema includes the same fields.
- [ ] The migration is idempotent and keeps S2 idempotency columns/indexes.

PRD promise: P4, P5

## Slice 2: Atomic Terminal Completion Helper

Priority: P0

Scope: Add a state helper that records terminal job status, return code/error,
terminal status, and terminal outcome JSON in one transaction, plus a terminal
event for replay/audit visibility.

Acceptance Criteria:

- [ ] Terminal status and terminal outcome are committed together.
- [ ] The helper rejects a terminal completion without an outcome.
- [ ] A `dual_agent_workflow_terminal_outcome` event is recorded for completed
  jobs.

PRD promise: P1, P4

## Slice 3: Worker-Side Terminal Ledger Write

Priority: P0

Scope: Thread `job_id` into the detached CLI request after idempotency-token
derivation. When the CLI produces a terminal workflow result, it opens the
configured state DB and records the terminal outcome through the atomic state
helper while still writing `result.json`.

Acceptance Criteria:

- [ ] The detached CLI payload can carry `job_id` without leaking it into
  workflow kwargs.
- [ ] Worker completion writes terminal outcome to the ledger before any parent
  poll is required.
- [ ] `result.json` is still written for compatibility.

PRD promise: P1, P2, P4

## Slice 4: Ledger-First Polling

Priority: P0

Scope: Change `poll_dual_agent_workflow_job` to read terminal results from the
ledger first and only read `result.json` when the ledger lacks an outcome.

Acceptance Criteria:

- [ ] Poll returns a completed job's ledger result even after `result.json` is
  deleted.
- [ ] Poll returns ledger status and result without marking a missing file as
  failure for completed jobs.
- [ ] Existing resume prompt behavior remains unchanged.

PRD promise: P1, P5

## Slice 5: Preserve Legacy Result-File Fallback

Priority: P1

Scope: Keep writing and reading `result.json` for compatibility. When polling a
pre-existing job with only a file result, return the file result and backfill the
ledger outcome.

Acceptance Criteria:

- [ ] The current result-file polling fixture still passes.
- [ ] A result-file-only old job polls successfully.
- [ ] The job row gains terminal outcome fields after fallback poll.

PRD promise: P2

## Slice 6: Reconcile Cache Discrepancies

Priority: P0

Scope: If ledger outcome and result-file cache differ, return the ledger outcome
and record an audit event describing the mismatch. Own the canonical terminal
outcome form used at write time and comparison time: parsed workflow result
object, redacted with supervisor redaction, serialized as sorted-key JSON.

Acceptance Criteria:

- [ ] Ledger/cache mismatch returns the ledger result.
- [ ] A `dual_agent_workflow_terminal_discrepancy` event is written.
- [ ] Comparison uses the canonical parsed/redacted terminal result object, not
  raw bytes or poll wrapper fields.
- [ ] Matching ledger/cache outcomes produce no discrepancy event.
- [ ] The mismatch does not overwrite the ledger or block the completed job.

PRD promise: P3
