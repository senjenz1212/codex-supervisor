# TDD Plan: Durable Substrate S2

## test_submit_dual_agent_workflow_job_dedupes_same_client_token

Maps to: P1, P3, Slice 1

RED: Call `submit_dual_agent_workflow_job` twice with the same `client_token`
and observe two job ids plus two fake `Popen` calls.

GREEN: Both responses carry the same `job_id`; launcher is called once; the job
row stores the normalized idempotency token.

## test_submit_dual_agent_workflow_job_derives_idempotency_for_legacy_callers

Maps to: P2, P5, Slice 2

RED: Submit the same logical payload twice without `client_token` and observe
duplicate detached jobs.

GREEN: Second call returns the first job, and the first job id still starts with
`workflow-`.

## test_submit_dual_agent_workflow_job_keeps_different_tokens_independent

Maps to: P4, Slice 4

RED: Submit equivalent payloads with different explicit tokens and accidentally
collapse them into one job.

GREEN: Two tokens produce two different job ids and two fake launcher calls.

## test_submit_dual_agent_workflow_job_concurrent_same_token_launches_once

Maps to: P3, Slice 3

RED: Use a thread pool to call the submit tool concurrently with the same token
and observe a duplicate launch.

GREEN: Exactly one unique job id is returned, one launcher call is made, and one
database row exists for the idempotency token.

## test_forward_migration_adds_workflow_job_idempotency

Maps to: P1, P3, Slice 1

RED: Open an old DB with `dual_agent_workflow_jobs` but no idempotency column and
observe no unique token enforcement.

GREEN: Migration adds `idempotency_token` and the unique index idempotently.

## Existing Regression Tests

Maps to: P5, Slice 4

RED: Existing submit/poll fixtures regress because idempotency changed the job
shape or result-file poll path.

GREEN: `test_submit_dual_agent_workflow_job_spawns_detached_worker_and_records_job`
and `test_poll_dual_agent_workflow_job_reads_durable_result_after_transport_loss`
remain green.

## Regression Commands

- `uv run pytest tests/test_dual_agent_workflow_driver.py tests/test_schema_migrations.py -q`
- `uv run --extra dev pytest -q`
