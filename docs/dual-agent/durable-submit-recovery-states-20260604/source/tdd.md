# TDD Plan

## Public Boundary

Use `submit_dual_agent_workflow_job`, `poll_dual_agent_workflow_job`,
`State.reserve_dual_agent_workflow_job`, and the SQLite migration path.

## Test Cases

### test_forward_migration_adds_workflow_job_idempotency

Maps to: ISS-1, P2
RED: Insert two non-terminal `dual_agent_workflow_jobs` rows with the same
`idempotency_token`; the second insert must raise `sqlite3.IntegrityError`.
GREEN: Add the active-token unique index in schema migration.

### test_reserve_dual_agent_workflow_job_eight_process_race_reserves_once

Maps to: ISS-1, P2
RED: Race eight processes through `State.reserve_dual_agent_workflow_job`; more
than one created row fails the test.
GREEN: Let the database constraint backstop the reservation flow and return the
existing row on conflict.

### test_submit_dual_agent_workflow_job_reserves_then_poll_spawns_worker

Maps to: ISS-2, P1; ISS-3, P3, P5
RED: Patch `subprocess.Popen` and call the MCP submit tool; any spawn or
request-file write during submit fails.
GREEN: Persist request payload on the row and return the reserved job metadata.

RED: Poll the reserved job and assert the request file is written, the durable
job row reaches `recovery_point=request_written` before `subprocess.Popen`, the
worker is spawned once, and the final returned recovery point is `spawned`.
GREEN: Persist the request-written phase before spawning the detached worker
and advance to `spawned` after pid persistence succeeds.

### test_poll_dual_agent_workflow_job_stale_spawn_claim_fails_without_respawn

Maps to: ISS-3, P4, P5
RED: Create a `request_written` job with a stale `spawn:*` recovery claim and no
persisted pid; polling must not call `Popen` because a prior worker may already
be alive without a recorded pid.
GREEN: Treat stale spawn claims as an ambiguous post-Popen durability gap and
record a terminal failure instead of double-spawning.

### test_poll_dual_agent_workflow_job_kills_worker_when_spawn_persist_fails

Maps to: ISS-3, P4, P5
RED: Let `Popen` return a process, then make persisting
`recovery_point=spawned` fail; the poll result must terminate the just-created
process, record a terminal failure, and a second poll must not spawn again.
GREEN: Wrap spawned-state persistence, clean up the process on failure, and
store the failure as the terminal outcome.

### test_poll_dual_agent_workflow_job_restarts_from_request_written

Maps to: ISS-3, P3
RED: Create a request-written job with no pid and poll; assert it spawns without
creating a second job.
GREEN: Spawn from durable request path when `recovery_point=request_written`.

### test_poll_dual_agent_workflow_job_records_terminal_failure_when_spawn_fails

Maps to: ISS-3, P5
RED: Make `Popen` raise `OSError`; polling must record `status=failed`,
`recovery_point=terminal`, and terminal outcome JSON.
GREEN: Use `complete_dual_agent_workflow_job` for spawn failures.

### test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome

Maps to: ISS-2, ISS-3, P4
RED: Complete a job, delete the result file, re-submit with the same token, and
assert the stored terminal outcome is returned without spawning.
GREEN: Reservation checks existing token rows before inserting and returns
terminal payloads.

## RED/GREEN Plan

RED: Add schema and reservation tests for active idempotency and process races.
GREEN: Add recovery columns, migration, active unique index, and conflict
reattach behavior.

RED: Update submit tests to fail on submit-side side effects.
GREEN: Move request-file writing and `Popen` out of submit and persist request
payload JSON on the row.

RED: Add poll recovery tests for reserved, request-written, failed spawn, and
terminal replay cases.
GREEN: Add the poll-side recovery driver and keep existing terminal-result
polling behavior.

RED: Add post-spawn failure tests for stale spawn claims and failure after
`Popen` but before spawned-state persistence.
GREEN: Fail ambiguous stale spawn claims terminally and terminate a just-created
worker if spawned-state persistence fails.

RED: Run the focused tests and then full suite.
GREEN: Keep all existing workflow, reviewer, and schema migration tests green.
