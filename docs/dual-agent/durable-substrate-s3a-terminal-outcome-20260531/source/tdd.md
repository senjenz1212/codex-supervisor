# TDD Plan: Durable Substrate S3a

## test_poll_dual_agent_workflow_job_reads_ledger_result_when_result_file_deleted

Maps to: P1, Slice 3

RED: Complete a detached job in the ledger, delete `result.json`, and observe
poll returning no result or marking the job failed.

GREEN: Poll returns the ledger terminal outcome, terminal status, and no file
dependency for the completed job.

## test_workflow_cli_records_terminal_outcome_in_ledger

Maps to: P1, P2, P4, Slice 3

RED: Run the detached CLI completion path with a `job_id` and observe that it
only writes `result.json`, leaving the job row without terminal outcome until a
future poll.

GREEN: The CLI keeps writing `result.json`, records the terminal outcome in the
ledger through the atomic completion helper before parent poll, and emits a
`dual_agent_workflow_terminal_outcome` audit event.

## test_poll_dual_agent_workflow_job_keeps_legacy_result_file_fallback

Maps to: P2, Slice 4

RED: Poll a pre-existing job with valid `result.json` but no ledger terminal
outcome and observe the new ledger-first path ignoring the legacy result.

GREEN: Poll reads the file, returns the result, updates job status, and
backfills the ledger terminal outcome through the atomic helper.

## test_poll_dual_agent_workflow_job_ledger_wins_over_result_file_cache

Maps to: P3, Slice 5

RED: Store one terminal outcome in the ledger, write a different `result.json`,
and observe poll returning the cache or silently replacing the ledger.

GREEN: Poll compares canonical parsed/redacted result objects, returns the
ledger outcome, and writes a `dual_agent_workflow_terminal_discrepancy` event.

## test_poll_dual_agent_workflow_job_does_not_emit_discrepancy_for_matching_cache

Maps to: P3, Slice 6

RED: Store a terminal outcome in the ledger, write an equivalent result object
to `result.json` with different formatting/key order, and observe poll emitting
a noisy discrepancy event.

GREEN: Poll canonicalizes the parsed/redacted workflow result object, returns
the ledger outcome, and emits no discrepancy event when the cache is equivalent.

## test_complete_dual_agent_workflow_job_requires_terminal_outcome

Maps to: P4, Slice 2

RED: Mark a job accepted/failed/completed without a terminal outcome and observe
the ledger exposing terminal status without result JSON.

GREEN: The state helper rejects terminal completion without an outcome and
commits status plus outcome together for valid completions.

## test_complete_dual_agent_workflow_job_rolls_back_status_when_terminal_event_fails

Maps to: P4, Slice 2

RED: Force the terminal-outcome event insert to fail and observe the job status
committed as terminal while `terminal_outcome_json` is missing or partial.

GREEN: The helper uses one transaction for job status, terminal outcome fields,
and terminal-outcome event; injected event failure rolls back the status flip and
outcome write together.

## test_forward_migration_adds_workflow_job_terminal_outcome_fields

Maps to: P4, P5, Slice 1

RED: Open an old DB with `dual_agent_workflow_jobs` but no terminal outcome
columns and observe missing ledger fields.

GREEN: Migration adds the terminal status, terminal outcome JSON, and recorded-at
columns idempotently.

## Existing Regression Tests

Maps to: P2, P5, Slice 4

RED: S2 submit idempotency or existing result-file polling regresses.

GREEN: `tests/test_dual_agent_workflow_driver.py` and
`tests/test_schema_migrations.py` remain green; full suite remains green.

## Regression Commands

- `uv run pytest tests/test_dual_agent_workflow_driver.py tests/test_schema_migrations.py -q`
- `uv run --extra dev pytest -q`
