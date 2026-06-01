# PRD: Durable Substrate S3a Terminal Outcome

## Problem Statement

Detached dual-agent workflow jobs currently persist their terminal result only
to `.handoff/workflow-jobs/<job_id>/result.json`. The SQLite ledger records job
status and paths, but not the authoritative terminal outcome. If the result
file is deleted, unavailable, or inconsistent with the job row, a reconnecting
client cannot reliably recover the final workflow result from the durable store.

S3a makes the detached workflow job's terminal outcome durable in the ledger of
record while keeping `result.json` as a compatibility cache for existing
operators and tools.

## Solution

Add terminal outcome fields to `dual_agent_workflow_jobs` and write them in the
same SQLite transaction as the terminal status update. The detached CLI worker
must receive its `job_id`, open the configured supervisor state DB, and record
the terminal outcome in the ledger after producing the workflow result. That
worker-side ledger write closes the unpolled-completion window where a result
file could be deleted before the parent process observes it.

`poll_dual_agent_workflow_job` must prefer the ledger outcome, fall back to
`result.json` only for pre-existing jobs without ledger outcome fields, and
record a discrepancy event when the legacy cache differs from the ledger. The
scope is strictly detached workflow job results; verdicts, actions, hook
requests, and other projections remain unchanged.

## User Stories

- As a reconnecting client, I can poll a completed detached job and get the
  final result even when `result.json` is missing.
- As an existing operator, I still get a written `result.json` file for
  compatibility with current scripts and manual inspection.
- As a reviewer, I can prove that a terminal job status and terminal outcome are
  written atomically.
- As an operator investigating drift, I can see when the legacy cache disagrees
  with the ledger and trust the ledger result.
- As a maintainer, I can open an old database and have it gain the new terminal
  outcome columns without rebuilding the ledger.

## PRD Promise Contracts

P1. ledger-terminal-outcome-survives-missing-result-file

- User-visible promise: A completed detached job's final result is readable from
  the SQLite ledger even if `result.json` is removed.
- Representative action: Run a detached workflow CLI payload with `job_id`,
  persist its terminal outcome in the ledger, delete the result file, then call
  `poll_dual_agent_workflow_job`.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: The worker writes the ledger terminal outcome at completion;
  poll returns the ledger result and terminal status without the file.
- Forbidden outcomes: Poll returns `result=None`, marks the job failed because
  the file is missing, or requires the legacy cache for a completed job.

P2. result-json-remains-compatibility-cache

- User-visible promise: Completed detached jobs still write `result.json`, and
  old result-file-only jobs still poll successfully.
- Representative action: Poll a pre-existing job row that has no ledger terminal
  outcome but has a valid `result.json`.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Poll reads the file, returns the result, and backfills the
  ledger outcome through the same atomic completion helper used by new jobs.
- Forbidden outcomes: Breaking old job fixtures, deleting the cache, or making
  clients migrate before polling old jobs.

P3. ledger-wins-on-discrepancy

- User-visible promise: If the ledger terminal outcome and result-file cache
  differ, the ledger is authoritative and the discrepancy is observable.
- Representative action: Store terminal outcome A in the ledger, write outcome B
  to `result.json`, then poll the job.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Poll returns outcome A and records a discrepancy event.
- Forbidden outcomes: Returning the stale cache, silently overwriting the ledger,
  or treating mismatch as an automatic workflow failure.

Comparison basis: ledger/cache equality is computed by parsing both outcomes as
objects, applying the same redaction/canonicalization used before ledger
storage, and comparing sorted-key JSON. Poll metadata such as resume prompt,
event id, or response wrapper fields is outside this comparison because those
fields are not part of the terminal workflow result.

P4. terminal-status-and-outcome-are-atomic

- User-visible promise: The ledger cannot expose a terminal job status without a
  matching terminal outcome for newly completed jobs.
- Representative action: Complete a detached job through the state helper.
- Public boundary: `state_ledger_api`
- Allowed outcomes: `status`, `terminal_status`, and `terminal_outcome_json`
  change in one transaction.
- Forbidden outcomes: A committed terminal status with null terminal outcome, or
  terminal outcome updates outside the job status transaction.

P5. scope-stays-detached-job-only

- User-visible promise: S3a does not migrate the rest of the supervisor to event
  sourcing or change gate/reviewer semantics.
- Representative action: Run existing dual-agent workflow tests and replay
  exports.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Existing verdict, action, hook request, gate, and reviewer
  behaviors remain unchanged.
- Forbidden outcomes: Projection rebuilds, altered gate algebra, or weakening
  P1/P2/P3/P13/P14.

## Implementation Decisions

- Store the durable terminal result on `dual_agent_workflow_jobs` with nullable
  terminal fields for backward compatibility.
- Add a forward migration so existing DBs get the terminal fields on open.
- Thread `job_id` into the detached CLI request after the idempotency token has
  been derived, so the job id does not affect S2 deduplication.
- Use a single state method for terminal completion so status and outcome are
  updated atomically by both the worker-side completion path and legacy
  poll-backfill path.
- Rely on SQLite/WAL and `BEGIN IMMEDIATE` in the state helper for cross-process
  serialization; the in-process `_write_lock` is only an additional local guard.
- Keep writing `result.json` as a cache and legacy interoperability artifact.
- Prefer ledger data in `poll_dual_agent_workflow_job`; use the file only when
  the ledger has no terminal outcome.
- Record `dual_agent_workflow_terminal_outcome` for durable completion and
  `dual_agent_workflow_terminal_discrepancy` for cache mismatch observability.

## Testing Decisions

- Test at the `submit/poll` supervisor tool boundary with a local `State`.
- Test the CLI completion helper so worker-side terminal ledger writes are
  proven independently of parent poll.
- Test state-level atomicity by requiring terminal status and outcome together.
- Test migration with an old `dual_agent_workflow_jobs` table.
- Preserve the S2 idempotent submit tests and existing result-file polling
  fixture.

## Out Of Scope

- Full event-sourcing or projection rebuilds (S3b).
- Event-tail cursor reads (S1), idempotent submit (S2), or resumable transport
  protocol (S5), except as assumptions.
- Cursor/reviewer reliability and gate algebra changes.
