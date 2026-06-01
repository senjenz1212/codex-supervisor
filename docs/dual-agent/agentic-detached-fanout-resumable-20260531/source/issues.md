# Issues: Agentic Detached Fan-Out Resume Safety

## Slice 1: Durable Receipt Hydration

PRD promises: P1, P2, P6
Priority: P0
Scope: Add a deterministic hydration path at the supervisor workflow boundary
that merges caller receipts, prior `dual_agent_agentic_worker_production`
ledger receipts, and replay-verifiable `.handoff/agentic-workers/<task>/`
receipt files before agentic production.

Deduplicate by receipt id and worker identity. Failed receipts are preserved for
audit but do not count as completed workers.

Acceptance criteria:
- [ ] Existing passing durable worker receipts are visible before the producer
      plans new work.
- [ ] Hydrated receipts are included in P13 inputs and remain replay-verifiable.
- [ ] Failed, timeout, or hash-mismatched receipts are retained as evidence but
      do not satisfy required roles.

First RED test: `test_run_dual_agent_workflow_hydrates_durable_agentic_worker_receipts_before_producer`

## Slice 2: Partial Fan-Out Resume

PRD promises: P1, P2, P6
Priority: P0
Scope: Teach `produce_agentic_worker_receipts` to reuse completed existing
subagent receipts and spawn only missing or failed roles.

If existing passing receipts already satisfy `min_subagents` and required roles,
the producer should skip new work. If only part of the roster is complete, it
should run the pending subset.

Acceptance criteria:
- [ ] Completed roles are counted once toward role coverage and minimum
      subagent counts.
- [ ] Missing roles are spawned; failed receipts are not counted as completion.
- [ ] Existing complete fan-outs skip worker spawning without dropping receipts.

First RED test: `test_produce_agentic_worker_receipts_reuses_completed_workers_and_spawns_missing_roles`

## Slice 3: Detached Reattach E2E

PRD promises: P3, P5, P6
Priority: P0
Scope: Extend detached submit and reconnect coverage for agentic policy using
the existing S2 idempotent submit, S3a ledger terminal outcome, and S5 catch-up
tools.

A retried submit with the same `client_token` must return the same job and not
launch a second CLI worker. The catch-up page must include worker
production/progress events that occurred after the saved cursor, and poll must
read the ledger terminal outcome when `result.json` is absent.

Acceptance criteria:
- [ ] Same token and payload returns the same `job_id` and current status.
- [ ] Reconnect catch-up returns missed worker events exactly once.
- [ ] Poll returns the ledger terminal outcome even when `result.json` is gone.

First RED test: `test_detached_agentic_reconnect_reuses_job_hydrates_receipts_and_catches_up`

## Slice 4: Orphan Worker Cleanup

PRD promises: P4
Priority: P1
Scope: Persist enough worker runtime metadata to reap stale worker subprocesses
by task and expose the cleanup result through supervisor-owned logs/events.

Add a task-level cleanup helper that discovers worker runtime records under
`.handoff/agentic-workers/<task>/`, terminates live timed-out pids, and records
cleanup in the agentic production event.

Acceptance criteria:
- [ ] Stale live pids are terminated and reported with durable log refs.
- [ ] In-budget live workers are left running.
- [ ] Dead or missing pids are recorded as skipped rather than treated as a
      shell-cleanup requirement.

First RED test: `test_agentic_worker_task_cleanup_discovers_and_reaps_stale_runtime_records`

## Slice 5: Progress Events

PRD promises: P3, P5
Priority: P1
Scope: Emit lightweight worker spawned/finished events around supervisor-owned
worker execution and expose them through transcript and catch-up reads.

Keep terminal `dynamic_subagent_result` receipts as the P13 authority.

Acceptance criteria:
- [ ] Worker spawned and finished facts are appended to the event ledger.
- [ ] `catch_up_dual_agent_workflow` returns those events in event id order.
- [ ] Transcript projection includes the events without making them P13
      authority.

First RED test: `test_agentic_worker_progress_events_are_available_to_catch_up`

## Slice 6: Regression Gates

PRD promises: P6
Priority: P0
Scope: Preserve default-off fan-out, required-policy fail-closed behavior,
P13 runtime-native hash verification, P14 synthesis, and downstream Cursor
review.

Keep default agentic policy off, required policy fail-closed, P13
runtime-native hash verification, and Cursor downstream review unchanged.

Acceptance criteria:
- [ ] Default workflow runs do not opt into agentic fan-out.
- [ ] Required policy still blocks insufficient, failed, self/solo, or
      non-runtime-native evidence.
- [ ] Cursor remains a downstream independent reviewer and no missing verdict is
      counted as accept.

First RED test: existing required-policy and default-off tests remain green.
