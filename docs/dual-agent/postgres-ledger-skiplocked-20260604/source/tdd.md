# TDD Plan: Postgres Ledger And Workflow Job Lane

## Cycle 1: SQLite remains default, Postgres URL selects the new lane

### test_state_uses_sqlite_for_filesystem_paths

### test_state_postgres_url_routes_to_postgres_lane

Public boundary: `supervisor_event_ledger`

RED:
- Add a test that `State(str(tmp_path / "state.db"))` remains the SQLite class.
- Add a test that a Postgres URL routes through the Postgres lane factory without opening SQLite path directories.
Maps to: ISS-1, P1
Test names: `test_state_uses_sqlite_for_filesystem_paths`, `test_state_postgres_url_routes_to_postgres_lane`

GREEN:
- Add DSN detection and a lazy Postgres store factory.

## Cycle 2: Per-run Postgres catch-up

### test_postgres_partitioned_per_run_catch_up

Public boundary: `supervisor_event_ledger`

RED:
- With an explicit Postgres test DSN, write interleaved events for `run-a` and `run-b`.
- Assert each run's first event has `event_id == 1`, later events carry `previous_event_id`, and `read_events_since` returns only the requested run.
Maps to: ISS-1, P4
Test name: `test_postgres_partitioned_per_run_catch_up`

GREEN:
- Add Postgres events and stream-sequence tables.
- Implement Postgres `write_event`, `read_events_since`, `latest_event_id`, and event-row lookup.

## Cycle 3: Multi-writer idempotent reservation

### test_postgres_multi_writer_double_submit_creates_one_job

### test_postgres_reserve_replays_terminal_token

Public boundary: `dual_agent_runner`

RED:
- With an explicit Postgres test DSN, spawn 8 reservation callers against one idempotency token.
- Assert exactly one caller reports `created=True`, every caller receives the same job id, and only one active row exists for the token.
- Add a SQL/schema assertion for the active-token partial unique index.
Maps to: ISS-2, P2
Test names: `test_postgres_multi_writer_double_submit_creates_one_job`, `test_postgres_reserve_replays_terminal_token`

GREEN:
- Implement Postgres reservation with terminal replay plus insert guarded by the active-token unique index; duplicate writers catch the constraint and reattach.

## Cycle 4: SKIP LOCKED multi-claimer

### test_postgres_claim_sql_uses_fenced_skip_locked_cte

### test_postgres_concurrent_skip_locked_claimers_get_disjoint_jobs

### test_postgres_claim_limit_is_bounded_by_cte

Public boundary: `dual_agent_runner`

RED:
- Add SQL-shape assertions: materialized CTE, `LIMIT` in the CTE, `FOR UPDATE SKIP LOCKED`, uniform `ORDER BY priority, created_at, id`.
- With Postgres DSN, reserve multiple jobs and run concurrent claimers; assert disjoint claims and no duplicate leases.
Maps to: ISS-3, P3
Test names: `test_postgres_claim_sql_uses_fenced_skip_locked_cte`, `test_postgres_concurrent_skip_locked_claimers_get_disjoint_jobs`, `test_postgres_claim_limit_is_bounded_by_cte`

GREEN:
- Add Postgres batch claim and keep the dispatcher's single-job path as `limit=1`.

## Cycle 5: Migration and production lane

### test_alembic_migration_and_make_target_exist

### test_postgres_schema_carries_idempotency_and_partitioned_catch_up

Public boundary: `target_config_load`

RED:
- Assert Alembic migration file exists and includes the idempotency index, SKIP-LOCKED-compatible columns, and per-run stream sequence table.
- Assert `make migrate` invokes Alembic.
- Assert config example documents PgBouncer transaction-pool DSN while keeping SQLite default.
Maps to: ISS-4, P5
Test names: `test_alembic_migration_and_make_target_exist`, `test_postgres_schema_carries_idempotency_and_partitioned_catch_up`

GREEN:
- Add Alembic config/files, Makefile target, optional Postgres dependencies, and config docs.

## Regression / Full Suite

- Run Postgres live tests when `CODEX_SUPERVISOR_POSTGRES_TEST_DSN` is available.
- Run SQLite workflow/job and event ledger tests.
- Run full suite.
- Run `git diff --check` and compile checks.
