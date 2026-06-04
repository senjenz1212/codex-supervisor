# TDD Grill Findings

### Finding 1: Live Postgres tests cannot be mandatory on every laptop

status: resolved

Resolved: The TDD plan requires live tests when `CODEX_SUPERVISOR_POSTGRES_TEST_DSN` is present, and always-on SQL-shape tests for the non-portable concurrency invariants. The full SQLite suite remains mandatory.

### Finding 2: A SQL-shape test is not a substitute for the multi-writer reserve test

status: resolved

Resolved: The idempotency cycle requires a real concurrent Postgres reservation test. The SQL-shape test only covers the claim query's CTE fence/order requirements.

### Finding 3: Per-run event ids must remain compatible with existing cursors

status: resolved

Resolved: The Postgres lane keeps the same `read_events_since(run_id, after_event_id)` public boundary. Only the meaning of `event_id` changes from global SQLite id to run-local Postgres id.

### Finding 4: Migration proof must be concrete

status: resolved

Resolved: The plan requires an Alembic migration file and `make migrate` target, not only runtime `CREATE TABLE IF NOT EXISTS` setup.
