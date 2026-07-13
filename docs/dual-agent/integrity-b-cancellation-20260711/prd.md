# PRD: INTEGRITY-B Process-Group Cancellation

## Problem Statement

Detached workflow workers start in a new session, but dispatcher cleanup signals
only the direct `Popen` process. A worker can therefore exit while its Claude or
Codex descendant continues running. The stale-lease reaper has a second gap: it
records terminal failure without signaling the persisted worker PID at all.
Both paths can leave an agent CLI mutating evidence after the supervisor says
the job stopped.

## Solution

Make the dispatcher terminate the worker process group, not only the session
leader. Resolve the worker PGID, send `SIGTERM`, wait for a bounded grace
period, escalate the same group to `SIGKILL`, and refuse to report successful
cleanup while the group still exists. Reuse that operation before a stale
spawned lease is marked failed. Treat a missing process group as an idempotent
success, including the case where the session leader is already dead but its
PID remains the known process-group ID.

## User Stories

1. As an operator, I want cancelling a detached worker to stop every agent CLI
   it launched so no hidden process keeps editing the repository.
2. As an operator, I want an expired worker lease to terminate its process tree
   before the job is persisted as failed.
3. As a maintainer, I want repeated cleanup attempts against an already-dead
   group to succeed harmlessly so reaper retries remain safe.

## PRD Promise Contracts

P1. Cancellation Removes the Worker Group

- User-visible promise: dispatcher cancellation leaves no live member of the
  detached worker's process group.
- Representative action: start a session-leader worker with a long-lived child,
  then invoke dispatcher termination.
- Public boundary: dispatcher worker lifecycle cleanup.
- Allowed outcomes: group-wide `SIGTERM`, bounded wait, group-wide `SIGKILL`
  when required, and an empty process group.
- Forbidden outcomes: terminating only the parent or returning while the child
  remains addressable by the original PGID.

P2. Stale-Lease Failure Kills Before Terminal Persistence

- User-visible promise: an expired spawned lease cannot be marked failed while
  its worker group is still executing.
- Representative action: persist a spawned job with an expired lease and a
  live parent/child process group, then run `reap_stale_leases`.
- Public boundary: `WorkflowJobDispatcher.reap_stale_leases`.
- Allowed outcomes: the process group exits first, followed by one terminal
  failed job outcome.
- Forbidden outcomes: terminal failure preceding cleanup, respawning the stale
  row, or leaving a descendant alive.

P3. Dead-Group Cleanup Is Idempotent

- User-visible promise: cleanup can be retried after the process group has
  already exited.
- Representative action: wait for a detached process to exit and call
  termination twice.
- Public boundary: dispatcher worker lifecycle cleanup and stale reaping.
- Allowed outcomes: both calls return without error and the group stays absent.
- Forbidden outcomes: surfacing `ProcessLookupError`, signaling the dispatcher
  group, or changing job state because cleanup was repeated.

## Implementation Decisions

- Keep the change inside `supervisor/workflow_job_dispatcher.py`; no state
  schema or `state.py` change is required.
- Rely on the existing `start_new_session=True` spawn invariant, under which the
  worker PID is also the process-group ID.
- If the leader PID is gone, probe the original PID as a PGID so an orphaned
  descendant can still be terminated.
- Poll group existence with signal `0`, reap the direct child when possible,
  and raise if the group survives `SIGKILL`.
- Refuse to signal the dispatcher's own process group.

## Testing Decisions

- Use real POSIX subprocesses rather than mocked signal calls.
- Synchronize on a child-written readiness file before cancellation.
- Require a worker that survives `SIGTERM` so the test proves the `SIGKILL`
  escalation, while the child proves group-wide signaling.
- Observe group absence at the exact state-completion boundary for stale leases.
- Retain existing fake-PID reaper tests to cover already-dead persisted workers.

## Out of Scope

- Adding a new MCP or AXI cancellation command.
- Changing workflow-job schema, terminal outcome semantics, or lease ownership.
- Cross-platform process-tree control on non-POSIX systems.
- Replacing the dispatcher with a durable workflow engine.
