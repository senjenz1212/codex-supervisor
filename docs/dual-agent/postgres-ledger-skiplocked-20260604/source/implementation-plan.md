# Implementation Plan

## Files / Modules To Touch

- `supervisor/postgres_state.py`
- `supervisor/state.py`
- `tests/test_postgres_ledger_lane.py`
- `migrations/env.py`
- `migrations/versions/20260604_0001_postgres_event_job_lane.py`
- `alembic.ini`
- `Makefile`
- `pyproject.toml`
- `uv.lock`
- `config.example.yaml`
- `docs/dual-agent/postgres-ledger-skiplocked-20260604/source/prd.md`
- `docs/dual-agent/postgres-ledger-skiplocked-20260604/source/issues.md`
- `docs/dual-agent/postgres-ledger-skiplocked-20260604/source/tdd.md`

## Steps

1. Add `supervisor/postgres_state.py` with a lazy optional `psycopg` dependency and the event/job methods used by durable submit, dispatch, and catch-up.
2. Add DSN routing in `State.__new__` so filesystem paths keep SQLite and Postgres URLs return `PostgresState`.
3. Implement Postgres schema SQL for events, per-run stream sequences, workflow jobs, active-token idempotency, and dispatchable-job indexes.
4. Implement reserve, event write/read, terminal outcome persistence, lease heartbeat, stale-lease helpers, parking, and batch SKIP LOCKED claims on `PostgresState`.
5. Add Alembic files plus `make migrate`, and document PgBouncer transaction-mode DSNs in the config example.
6. Add SQL-shape, SQLite-default, and optional-live Postgres tests.
7. Run focused tests, optional Postgres tests when possible, the full SQLite suite, diff-check, compile check, then submit through the durable supervised workflow.

## Risks

- The live Postgres tests require an operator-provided `CODEX_SUPERVISOR_POSTGRES_TEST_DSN`; without it the suite proves SQL shape and SQLite rollback behavior but cannot execute MVCC races locally.
- The Postgres lane intentionally covers only event/job storage. Other supervisor tables still need SQLite or a future migration slice before the whole daemon can run exclusively on Postgres.
- PgBouncer transaction pooling is documented and expected for production, but not launched by this repository.
- Partial unique-index behavior must preserve terminal replay before insert; otherwise completed retry tokens could accidentally create new jobs.

## Traceability

- P1 / ISS-1: `test_state_uses_sqlite_for_filesystem_paths`, `test_state_postgres_url_routes_to_postgres_lane`.
- P2 / ISS-2: `test_postgres_multi_writer_double_submit_creates_one_job`, `test_postgres_reserve_replays_terminal_token`.
- P3 / ISS-3: `test_postgres_claim_sql_uses_fenced_skip_locked_cte`, `test_postgres_concurrent_skip_locked_claimers_get_disjoint_jobs`, `test_postgres_claim_limit_is_bounded_by_cte`.
- P4 / ISS-1: `test_postgres_partitioned_per_run_catch_up`.
- P5 / ISS-4: `test_alembic_migration_and_make_target_exist`, `test_postgres_schema_carries_idempotency_and_partitioned_catch_up`.
