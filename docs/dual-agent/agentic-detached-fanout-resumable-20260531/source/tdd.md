# TDD Plan: Agentic Detached Fan-Out Resume Safety

## Public Boundary

The first proof uses `supervisor_tool_api` through
`run_dual_agent_workflow`, `submit_dual_agent_workflow_job`,
`catch_up_dual_agent_workflow`, and `poll_dual_agent_workflow_job`. Helper tests
for `supervisor.agentic_executor` and `supervisor.agentic_workers` are allowed
only after the workflow/tool boundary test exists.

## Test Cases

### test_run_dual_agent_workflow_hydrates_durable_agentic_worker_receipts_before_producer

Maps to: ISS-1, P1, P6

RED: Seed a prior replay-verifiable worker receipt in the ledger and/or
`.handoff/agentic-workers/<task>/`, then run `run_dual_agent_workflow` with
required agentic policy. Assert the producer sees `existing_receipt_count > 0`
before planning and P13 receives the hydrated receipt.

GREEN: Add durable hydration before `produce_agentic_worker_receipts`, merge the
hydrated receipts into P13 input, and record hydration counts in the workflow
route or production event.

### test_produce_agentic_worker_receipts_reuses_completed_workers_and_spawns_missing_roles

Maps to: ISS-2, P1, P2

RED: Call `produce_agentic_worker_receipts` with one existing passing
`codebase_audit` receipt and a planned roster containing `codebase_audit` plus
`independent_reviewer`. Assert the current implementation skips all work or
duplicates the completed worker.

GREEN: Count passing existing workers toward `min_subagents` and required role
coverage; launch only the missing `independent_reviewer`. Preserve failed
existing receipts as audit evidence but do not count them as complete.

### test_detached_agentic_reconnect_reuses_job_hydrates_receipts_and_catches_up

Maps to: ISS-3, P3, P5

RED: Submit a detached required-agentic workflow with a stable `client_token`,
record a cursor, append or hydrate a worker receipt during the simulated
disconnect, submit again with the same token, catch up, and poll after deleting
`result.json`. Assert reattach works but no worker receipt hydration/progress is
visible.

GREEN: The second submit returns the same job without a second `Popen`; catch-up
returns worker production/progress and terminal outcome events; poll returns the
ledger terminal outcome even without `result.json`.

### test_agentic_worker_task_cleanup_discovers_and_reaps_stale_runtime_records

Maps to: ISS-4, P4

RED: Create `.handoff/agentic-workers/<task>/<worker>/` runtime metadata for a
stale live pid, an in-budget live pid, and a dead pid. Assert no task-level
cleanup can discover them.

GREEN: Add a task cleanup helper that discovers runtime metadata, terminates
only stale live pids, and reports cleaned/active/skipped records with log refs.

### test_agentic_worker_progress_events_are_available_to_catch_up

Maps to: ISS-5, P3, P5

RED: Run an agentic producer with an injected progress callback and assert no
worker spawned/finished events are written to the ledger/catch-up stream.

GREEN: Emit `dual_agent_agentic_worker_progress` events for spawned and
finished workers. Add the event kind to transcript and catch-up/read allowlists.

### test_hydrated_runtime_native_receipts_still_hash_verify_and_required_policy_blocks_failures

Maps to: ISS-1, ISS-6, P6

REGRESSION: Hydrated receipts must replay through P13 just like freshly
produced receipts. Passing supervisor-owned refs derive `runtime_native`; failed
or timeout receipts remain non-passing and block required policy.

### Existing regression tests

Keep these green:

- `test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts`
- `test_run_dual_agent_workflow_required_policy_still_blocks_without_executor_receipts`
- `test_agentic_required_blocks_solo_execution_before_lead`
- S2 idempotent submit tests
- S3a ledger terminal outcome tests
- S5 reconnect/catch-up tests
- full `uv run --extra dev pytest -q`

## RED/GREEN Order

1. Add hydration boundary test and helper-level hydration tests.
2. Implement receipt discovery/deduplication from ledger and `.handoff`.
3. Add partial fan-out resume test, then update producer filtering.
4. Add task-level orphan cleanup discovery and runtime metadata persistence.
5. Add progress event callback and catch-up/transcript allowlist coverage.
6. Add detached reconnect e2e regression combining S2, S3a, S5, and fan-out
   receipt hydration.
7. Run focused tests, then the full suite.
