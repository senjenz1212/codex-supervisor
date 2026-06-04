# Implementation Plan

## Files / Modules To Touch

- `supervisor/state.py`
- `supervisor/schema_migrations.py`
- `supervisor/workflow_job_dispatcher.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_workflow_cli.py`
- `pyproject.toml`
- `tests/test_dual_agent_workflow_driver.py`
- `tests/test_schema_migrations.py`
- `docs/dual-agent/dispatcher-leases-admission-20260604/source/*.md`
- `docs/dual-agent/dispatcher-leases-admission-20260604/skill-receipts.json`

## Risks

- Poll compatibility could accidentally become a second spawn implementation.
  Mitigation: route poll-side redrive through `WorkflowJobDispatcher`.
- Spawned lease expiry cannot safely respawn because the worker may still be
  live. Mitigation: reaper completes from result file if available or fails the
  spawned job; it does not respawn `spawned` rows.
- Retry storms can occur if spawn failures loop immediately. Mitigation:
  dispatcher owns `dispatch_attempts`, `next_dispatch_at`, capped backoff, and
  max-attempt parking.
- A wrong worker could extend another worker's lease. Mitigation: heartbeat is
  a compare-and-update on matching `leased_by`.
- Schema changes must not break existing state DBs. Mitigation: forward-only
  migration and existing migration tests.

## Traceability

- P1 -> `test_dispatcher_claims_reserved_job_and_spawns_worker`
- P1 -> `test_dispatcher_restarts_from_request_written`
- P1 -> `test_dispatcher_run_forever_repeats_reaper_and_dispatch_until_stopped`
- P1 -> `test_dispatcher_cli_once_runs_reaper_and_dispatch`
- P1 -> `test_dispatcher_cli_without_once_runs_long_lived_loop`
- P2 -> `test_heartbeat_extends_lease_for_matching_worker`
- P2 -> `test_workflow_job_lease_heartbeat_runs_until_worker_lease_rejected`
- P3 -> `test_dispatcher_reaper_reclaims_expired_pre_spawn_lease`
- P3 -> `test_dispatcher_reaper_fails_dead_spawned_worker`
- P3 -> `test_poll_dual_agent_workflow_job_uses_dispatcher_bridge`
- P4 -> `test_dispatcher_admission_cap_prevents_claim_when_full`
- P4 -> `test_dispatcher_budget_hook_parks_before_spawn`
- P5 -> `test_dispatcher_retryable_spawn_failure_uses_capped_backoff`
- P5 -> `test_dispatcher_retryable_spawn_failure_caps_backoff_after_jitter`
- P5 -> `test_dispatcher_retryable_spawn_failure_parks_at_attempt_cap`
- P5 -> `test_dispatcher_poison_job_parks_without_retry_loop`
- Migration -> `test_forward_migration_adds_workflow_job_dispatcher_leases`

## Steps

1. Add dispatcher lease schema and state helpers.
2. Add the single-writer workflow job dispatcher with one-shot and long-lived
   loop entrypoints.
3. Route MCP poll compatibility through the dispatcher while leaving submit as
   a pure reservation.
4. Add detached worker heartbeat support.
5. Add focused dispatcher/migration tests and run full suite.

## Evidence

- Focused dispatcher and migration tests: 19 passed.
- Workflow driver plus schema migration suites: 136 passed.
- Full test suite: 723 passed.
- `git diff --check`: passed.
- `python3 -m compileall -q supervisor mcp_tools`: passed.
