# Implementation Plan: Durable Substrate S3a

## Files / Modules To Touch

- `supervisor/state.py`
  - Add terminal outcome fields to `dual_agent_workflow_jobs`.
  - Add `complete_dual_agent_workflow_job` for atomic terminal status/outcome
    writes.
  - Canonicalize terminal outcomes by redacting and sorted-key serializing the
    parsed workflow result object before ledger storage.
  - Preserve generic job update for non-terminal state transitions.
- `supervisor/schema_migrations.py`
  - Add a forward migration for old ledgers so the new fields exist on open.
- `mcp_tools/codex_supervisor_stdio.py`
  - Add `job_id` to the detached CLI request after idempotency reservation.
  - Make `poll_dual_agent_workflow_job` ledger-first.
  - Backfill ledger outcome from legacy `result.json` when needed.
  - Return ledger outcome on canonicalized cache mismatch and record a
    discrepancy event.
  - Avoid discrepancy events when cache and ledger canonical outcomes match.
- `mcp_tools/codex_supervisor_workflow_cli.py`
  - Ignore `job_id` for workflow kwargs but use it after workflow completion to
    persist terminal outcome to the configured state DB.
  - Keep writing `result.json` for compatibility.
- `tests/test_dual_agent_workflow_driver.py`
  - Add ledger-first, legacy fallback/backfill, and ledger-wins discrepancy
    tests.
- `tests/test_schema_migrations.py`
  - Add old-DB migration coverage for terminal outcome fields.

## Risks

- Atomicity risk: terminal status and terminal outcome must not be separate
  commits for new completions.
- Unpolled-window risk: poll-only backfill cannot recover a deleted result file
  before first poll, so the worker itself must write the ledger.
- Compatibility risk: old file-only jobs must keep polling successfully.
- Authority risk: cache mismatch must not silently change the ledger result.
- Noise risk: discrepancy comparison must canonicalize parsed/redacted result
  objects instead of comparing raw file bytes.
- Scope risk: terminal outcome durability must not turn into S3b projection
  rebuilds or gate semantic changes.

## Traceability

- P1 / `test_poll_dual_agent_workflow_job_reads_ledger_result_when_result_file_deleted`:
  completed jobs poll from the ledger without `result.json`.
- P1 / `test_workflow_cli_records_terminal_outcome_in_ledger`: the detached CLI
  writes ledger terminal outcome at completion before parent poll.
- P2 / `test_poll_dual_agent_workflow_job_keeps_legacy_result_file_fallback`:
  old file-only jobs still work and backfill.
- P3 / `test_poll_dual_agent_workflow_job_ledger_wins_over_result_file_cache`:
  ledger result wins and mismatch is audited.
- P3 / `test_poll_dual_agent_workflow_job_does_not_emit_discrepancy_for_matching_cache`:
  equivalent cache formatting/key order does not create audit noise.
- P4 / `test_complete_dual_agent_workflow_job_requires_terminal_outcome`:
  terminal status and outcome are an atomic state helper contract.
- P4 / `test_complete_dual_agent_workflow_job_rolls_back_status_when_terminal_event_fails`:
  status, outcome, and terminal event roll back together on injected failure.
- P5 / focused and full regression suites: existing gates, reviewers, submit
  idempotency, and replay remain unchanged.

## Out Of Scope

- Full event-sourcing / projection rebuild.
- S1 event tail, S2 submit idempotency, and S5 reconnect protocol changes.
- Cursor/reviewer reliability changes.
