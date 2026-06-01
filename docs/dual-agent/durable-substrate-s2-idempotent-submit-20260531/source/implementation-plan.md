# Implementation Plan: Durable Substrate S2

## Files / Modules To Touch

- `supervisor/state.py`
  - Add `idempotency_token` to `dual_agent_workflow_jobs`.
  - Add a unique idempotency-token index.
  - Add atomic reserve/read helpers for detached workflow jobs.
- `supervisor/schema_migrations.py`
  - Add a forward migration for existing DBs so old ledgers gain the new column
    and unique index.
- `mcp_tools/codex_supervisor_stdio.py`
  - Add optional `client_token` to detached submit.
  - Derive a stable token when absent.
  - Reserve before spawning and return an existing job without launching.
  - Thread `client_token` through the MCP tool wrapper.
- `tests/test_dual_agent_workflow_driver.py`
  - Add submit idempotency, different-token, derived-key, concurrent race, and
    existing poll compatibility tests.
- `tests/test_schema_migrations.py`
  - Add old-DB migration coverage for the job idempotency column and unique
    index.

## Risks

- Atomicity risk: a check-then-insert outside a transaction can double-spawn
  workers. The implementation must reserve the job before `Popen`.
- Compatibility risk: existing callers omit `client_token`. The no-token path
  must derive a key and preserve `workflow-<hex>` job ids.
- Over-dedup risk: deduping only by `run_id` can collapse unrelated submits.
  Derived keys must include a canonical request hash.
- Migration risk: `CREATE TABLE IF NOT EXISTS` does not alter old DBs, so a
  forward migration is required.

## Traceability

- P1 / test_submit_dual_agent_workflow_job_dedupes_same_client_token: explicit same-token retry returns same job and one
  launcher call.
- P2 / test_submit_dual_agent_workflow_job_derives_idempotency_for_legacy_callers: no-token repeated logical submit derives a stable key and
  dedups while keeping job id format.
- P3 / test_submit_dual_agent_workflow_job_concurrent_same_token_launches_once: concurrent same-token submit is atomic.
- P4 / test_submit_dual_agent_workflow_job_keeps_different_tokens_independent: distinct tokens create distinct jobs.
- P5 / test_forward_migration_adds_workflow_job_idempotency: old ledgers migrate safely while existing poll/result-file behavior remains green.

## Out Of Scope

- Event append idempotency.
- Durable terminal outcome storage.
- Resumable transport protocol.
- Gate or reviewer semantics.
