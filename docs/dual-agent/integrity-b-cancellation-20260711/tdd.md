# TDD Plan

## Public Boundary

Exercise `WorkflowJobDispatcher.reap_stale_leases` for stale workers and the
dispatcher-owned worker lifecycle cleanup seam for cancellation. Use real
session/process-group behavior so the tests can distinguish parent-only
termination from descendant-safe cancellation.

## Test Cases

### test_cancel_term_kill_removes_worker_process_group_and_descendant

Maps to: ISS-1, ISS-3, P1

RED: A session-leader worker forks a child in the same PGID. The current
implementation terminates and waits for only the direct `Popen`; the assertion
observes that the original process group still exists.

GREEN: Resolve the PGID, send group `SIGTERM`, wait, escalate the group to
`SIGKILL`, reap the direct worker, and return only after the PGID and child PID
are absent.

Observed RED: `assert not _process_group_exists(pgid)` failed because the group
remained live after direct-process termination.

### test_stale_lease_kills_process_group_before_marking_job_failed

Maps to: ISS-2, ISS-3, P2

RED: Persist an expired spawned lease backed by a real worker/child group and
observe group liveness from `complete_dual_agent_workflow_job`. The observation
is false because `_fail_spawned` writes failure without signaling the worker.

GREEN: Terminate the persisted PID's process group at the start of
`_fail_spawned`, then write the unchanged failed terminal outcome and event.

Observed RED: the completion-boundary observation was `[False]` instead of
`[True]`.

### test_terminating_already_dead_process_group_is_idempotent

Maps to: ISS-1, ISS-2, P3

RED: An exited session leader makes `getpgid` and `killpg` report a missing
process. Cleanup must not translate that normal retry condition into an error.

GREEN: Treat a missing leader and missing PGID as successful cleanup; repeated
calls leave the group absent.

Observed sequence: this contract test was added after the first cancellation
implementation and was immediately GREEN, confirming that the earlier minimal
group helper already covered the required no-op behavior.

## RED/GREEN Execution

1. RED: add the real parent/child cancellation test; observe the descendant
   group surviving.
2. GREEN: implement group TERM/wait/KILL/final-check cleanup; rerun the one test.
3. RED: add the stale-lease ordering test; observe terminal persistence while
   the group is live.
4. GREEN: call the same group cleanup before stale failure persistence.
5. Add the explicit dead-group idempotency contract and run all three focused
   tests plus existing dispatcher reaper regressions.

## Focused Verification

- `uv run pytest -q tests/test_workflow_job_dispatcher_cancellation.py`
- Existing dispatcher nodes in `tests/test_dual_agent_workflow_driver.py`,
  including spawn, pre-spawn reclaim, dead spawned worker, and service loop.
- `python3 -m py_compile supervisor/workflow_job_dispatcher.py tests/test_workflow_job_dispatcher_cancellation.py`
- `git diff --check`
