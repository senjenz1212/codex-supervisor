# TDD Plan

## Public Boundary

Use `WorkflowJobDispatcher.run_once`, `WorkflowJobDispatcher.reap_stale_leases`,
the workflow-job `State` lease APIs, the detached worker heartbeat helper, and
the existing MCP `submit_dual_agent_workflow_job` / `poll_dual_agent_workflow_job`
compatibility boundary.

## Test Cases

### test_dispatcher_claims_reserved_job_and_spawns_worker

Maps to: ISS-2, P1
RED: Reserve a job and run one dispatcher tick with `Popen` faked; without a
dispatcher, the job remains reserved.
GREEN: Dispatcher claims the row, writes request JSON, spawns once, and persists
`recovery_point=spawned`, pid, lease fields, and running status.

### test_dispatcher_restarts_from_request_written

Maps to: ISS-2, P1, P3
RED: Seed a `request_written` job with no pid; dispatcher must spawn it exactly
once.
GREEN: Dispatcher claims the request-written phase and persists spawned state.

### test_heartbeat_extends_lease_for_matching_worker

Maps to: ISS-1, ISS-4, P2
RED: Heartbeat with a matching `leased_by` must extend `lease_expires_at`; a
wrong owner must not.
GREEN: Add compare-and-update heartbeat state API and worker heartbeat helper.

### test_workflow_job_lease_heartbeat_runs_until_worker_lease_rejected

Maps to: ISS-4, P2
RED: The detached worker heartbeat wrapper is untested; a worker can start
without proving it heartbeats under its spawned lease owner.
GREEN: `WorkflowJobLeaseHeartbeat` sends an immediate heartbeat, continues on a
background interval, stops when the lease owner is rejected, and joins cleanly.

### test_dispatcher_reaper_reclaims_expired_pre_spawn_lease

Maps to: ISS-1, ISS-2, P3
RED: A reserved job with expired lease remains stuck.
GREEN: Reaper clears the stale lease, and a dispatcher tick reclaims and drives
the job.

### test_dispatcher_reaper_fails_dead_spawned_worker

Maps to: ISS-2, P3
RED: A spawned row with expired lease or dead pid remains running forever.
GREEN: Reaper records terminal failure without respawning.

### test_dispatcher_admission_cap_prevents_claim_when_full

Maps to: ISS-3, P4
RED: With active spawned leases at capacity, dispatcher still claims a new
reserved row.
GREEN: Dispatcher returns backpressure and leaves the new row unclaimed.

### test_dispatcher_retryable_spawn_failure_uses_capped_backoff

Maps to: ISS-3, P5
RED: `Popen` failure loops immediately or fails without a retry schedule.
GREEN: Dispatcher clears the lease, increments attempts, sets
`next_dispatch_at`, and does not retry before that time.

### test_dispatcher_retryable_spawn_failure_caps_backoff_after_jitter

Maps to: ISS-3, P5
RED: Exponential retry plus jitter can exceed `max_backoff_s`, making the cap
non-authoritative.
GREEN: Dispatcher caps the jittered delay at `max_backoff_s`, so repeated
retry schedules never exceed the configured cap.

### test_dispatcher_retryable_spawn_failure_parks_at_attempt_cap

Maps to: ISS-3, P5
RED: Repeated retryable spawn failures schedule forever and never hit the
configured max-attempt cap.
GREEN: The final failed attempt records `dispatch_attempts`, parks the job with
`max_dispatch_attempts_exceeded`, clears the lease, and a later tick does not
reclaim it.

### test_dispatcher_budget_hook_parks_before_spawn

Maps to: ISS-3, P4, P5
RED: A job that exceeds the per-run budget hook still writes/spawns a worker.
GREEN: Dispatcher parks the row with `budget_cap_exceeded`, never calls Popen,
and excludes the job from future claims.

### test_dispatcher_poison_job_parks_without_retry_loop

Maps to: ISS-3, P5
RED: Malformed request payload is retried forever.
GREEN: Dispatcher sets `status=parked`, `parked_reason`, clears the lease, and
excludes the row from future claims.

### test_dispatcher_cli_once_runs_reaper_and_dispatch

Maps to: ISS-4, P1
RED: The long-lived dispatcher console entrypoint can exist in `pyproject.toml`
without proving the `--once` tick constructs the dispatcher and runs reaper plus
dispatch.
GREEN: The CLI loads config/state, passes dispatcher id, admission cap, and
lease ttl into `WorkflowJobDispatcher`, emits a one-tick JSON result, and exits
0.

### test_dispatcher_run_forever_repeats_reaper_and_dispatch_until_stopped

Maps to: ISS-4, P1
RED: The dispatcher may expose only a one-shot tick and fail to prove the
long-lived service loop repeatedly reaps stale leases and dispatches jobs.
GREEN: `WorkflowJobDispatcher.run_forever` calls reaper and dispatch each tick
until a stop event is set.

### test_dispatcher_cli_without_once_runs_long_lived_loop

Maps to: ISS-4, P1
RED: The console entrypoint may only support `--once`, leaving production
long-lived mode untested.
GREEN: CLI invocation without `--once` constructs the dispatcher and delegates
to `run_forever` with the configured interval.

### test_poll_dual_agent_workflow_job_uses_dispatcher_bridge

Maps to: ISS-2, P1, P3
RED: Poll compatibility can reintroduce a second spawn implementation instead
of delegating to the single dispatcher surface.
GREEN: `poll_dual_agent_workflow_job` constructs `WorkflowJobDispatcher`, calls
`reap_stale_leases`, and drives the exact requested job id through `run_once`.

### test_forward_migration_adds_workflow_job_dispatcher_leases

Maps to: ISS-1, P2
RED: Existing databases do not gain dispatcher lease, heartbeat, retry, park,
dispatchable-index, or idempotency uniqueness support.
GREEN: Forward migration v6 adds the lease/retry/park columns, dispatchable
index, and DB-enforced idempotency uniqueness needed by the dispatcher.

## RED/GREEN Plan

RED: Add lease schema tests for migration and heartbeat owner matching.
GREEN: Add forward migration plus state lease/heartbeat helpers.

RED: Add dispatcher happy-path tests for reserved and request-written rows.
GREEN: Extract the Layer 0 request-write/spawn logic into a dispatcher module
and route poll compatibility through it.

RED: Add stale lease, admission, retry, and poison tests.
GREEN: Implement reaper, active-cap counting, backoff, budget hook, and park
state, including max-attempt parking.

RED: Add CLI/worker heartbeat tests.
GREEN: Add dispatcher `--once`, long-lived loop, no-`--once` CLI, and detached
worker heartbeat coverage.

RED: Run focused tests, then full suite.
GREEN: Keep existing workflow, reviewer, and migration tests green.
