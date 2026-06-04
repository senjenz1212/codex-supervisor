# Implementation Plan

## Files / Modules To Touch

- `supervisor/state.py`
- `supervisor/schema_migrations.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_schema_migrations.py`
- `docs/dual-agent/durable-submit-recovery-states-20260604/source/prd.md`
- `docs/dual-agent/durable-submit-recovery-states-20260604/source/issues.md`
- `docs/dual-agent/durable-submit-recovery-states-20260604/source/tdd.md`
- `docs/dual-agent/durable-submit-recovery-states-20260604/source/grill-findings.md`
- `docs/dual-agent/durable-submit-recovery-states-20260604/source/grill-findings-tdd.md`

## Risks

- Moving spawn out of submit changes existing test expectations and could make
  callers that never poll observe only a reserved job. This is accepted for
  Layer 0 and documented as the future Layer 0.5 dispatcher boundary.
- A partial active idempotency index can allow raw SQL terminal duplicates, so
  the reservation API must still prefer existing token rows and replay terminal
  outcomes.
- Poll-side spawning could accidentally fail fake-pid tests if it immediately
  applies dead-pid recovery in the same call; tests must pin the intended
  running response after a successful spawn.
- A worker that is created by `Popen` but not persisted as `spawned` cannot be
  safely rediscovered from SQLite. Layer 0 must fail that ambiguous spawn claim
  rather than re-drive it and risk a duplicate worker.
- Legacy databases need forward migrations that do not fail before new columns
  exist.

## Traceability

- P1 -> test_submit_dual_agent_workflow_job_reserves_then_poll_spawns_worker
- P2 -> test_forward_migration_adds_workflow_job_idempotency
- P2 -> test_reserve_dual_agent_workflow_job_eight_process_race_reserves_once
- P3 -> test_submit_dual_agent_workflow_job_reserves_then_poll_spawns_worker
- P3 -> test_poll_dual_agent_workflow_job_restarts_from_request_written
- P4 -> test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome
- P4 -> test_poll_dual_agent_workflow_job_kills_worker_when_spawn_persist_fails
- P4 -> test_poll_dual_agent_workflow_job_stale_spawn_claim_fails_without_respawn
- P5 -> test_poll_dual_agent_workflow_job_records_terminal_failure_when_spawn_fails
- P5 -> test_poll_dual_agent_workflow_job_kills_worker_when_spawn_persist_fails
- P5 -> test_poll_dual_agent_workflow_job_stale_spawn_claim_fails_without_respawn

## Steps

1. Add schema migration and state helpers for recovery phases and active
   idempotency.
2. Change submit to reserve only and return recovery metadata.
3. Add poll-side recovery driver for request writing, spawn, and terminal
   failure.
4. Update focused tests and run the full suite.
