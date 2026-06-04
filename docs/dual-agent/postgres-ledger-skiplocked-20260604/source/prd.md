# PRD: Postgres Ledger And Workflow Job Lane

## Problem Statement

The supervisor can now make detached workflow submission durable on SQLite and can spawn reserved jobs through a single long-lived SQLite dispatcher. That is correct for local development, but it does not give the supervisor a multi-writer lane for roughly 100 concurrent agents. SQLite's file writer serializes submit and claim behavior; the same SELECT-then-act assumptions do not hold when multiple worker processes write through Postgres MVCC.

The operator needs a Postgres lane that preserves the existing local default while adding database-enforced idempotency, bounded multi-claimer dispatch with `FOR UPDATE SKIP LOCKED`, and per-run catch-up streams so transport recovery no longer depends on one global write sequence.

## Solution

Add an opt-in Postgres state lane behind the same workflow-job and event-ledger method surface used by the dispatcher and MCP tools. SQLite remains the default when `supervisor.state_db` is a file path. A Postgres URL selects the Postgres lane, which applies an Alembic migration and uses Postgres-native concurrency primitives.

The Postgres lane will:

- enforce active workflow-job idempotency with a partial unique index on non-terminal `idempotency_token`;
- reserve jobs durably before spawn, replaying existing active or terminal jobs by token;
- claim dispatchable jobs using a materialized CTE with `FOR UPDATE SKIP LOCKED`, `LIMIT`, and a uniform order;
- keep leases, heartbeats, backoff, and poison parking compatible with the Layer 0.5 dispatcher;
- write events with per-run `event_id` and `previous_event_id` values while keeping a global id/timestamp for audit-friendly ordering;
- provide an Alembic migration path and `make migrate` entrypoint for the production lane.

## User Stories

1. As the operator, I want local development to keep using SQLite by default, so that ordinary testing stays lightweight.
2. As the operator, I want Postgres to be opt-in through configuration, so that production can scale without changing every call site.
3. As a submitter, I want duplicate client-token retries to create exactly one active job under real multi-writer concurrency, so that dropped transports do not double-spawn.
4. As a dispatcher operator, I want many claimers to fetch disjoint jobs, so that the system can scale beyond one SQLite dispatcher.
5. As a worker operator, I want claim queries to preserve the same order everywhere, so that claimers avoid deadlocks.
6. As a worker operator, I want claim queries bounded before update, so that planner behavior cannot over-claim beyond the requested limit.
7. As an event-tail consumer, I want a per-run cursor, so that one noisy run does not impose a global sequence bottleneck on every other run.
8. As a recovery tool, I want event catch-up after reconnect to remain deterministic, so that transport drops can resume by run-specific cursor.
9. As a production operator, I want the Postgres lane documented as PgBouncer-fronted, so that 100 agents do not each hold direct backend connections.
10. As a reviewer, I want SQLite tests to remain green, so that the new lane has a clear rollback path.

## PRD Promise Contracts

P1. SQLite default remains local
User-visible promise: A normal config with a filesystem `state_db` continues to construct the existing SQLite-backed `State` and pass the existing suite.
Representative prompts or actions: `State("/tmp/state.db")`; `uv run pytest -q`.
Public boundary: supervisor_event_ledger and dual_agent_runner.
Allowed outcomes: SQLite tables and methods behave as before; Postgres-specific tests skip when no Postgres DSN is supplied.
Forbidden outcomes: default config requires Postgres, Alembic, PgBouncer, Docker, or a live database.
Related user stories: 1, 2, 10

P2. Postgres reservations are idempotent under multi-writer submit
User-visible promise: Concurrent Postgres submit retries with the same idempotency token yield one active workflow job and all other callers reattach.
Representative prompts or actions: 8 parallel `reserve_dual_agent_workflow_job(...)` calls against one Postgres DSN and token.
Public boundary: dual_agent_runner.
Allowed outcomes: exactly one created row; all retries return the same job; terminal-token replay returns the terminal row.
Forbidden outcomes: duplicate active jobs, SELECT-empty race, or dedup relying on Python locks.
Related user stories: 2, 3

P3. Postgres claimers use SKIP LOCKED without over-claiming
User-visible promise: Multiple dispatchers can claim available jobs concurrently and receive disjoint sets bounded by the requested limit.
Representative prompts or actions: concurrent `claim_dual_agent_workflow_jobs_for_dispatch(limit=n)` calls.
Public boundary: dual_agent_runner.
Allowed outcomes: disjoint claims; no duplicate leases; claim SQL contains a materialized CTE, `LIMIT`, `FOR UPDATE SKIP LOCKED`, and a uniform order.
Forbidden outcomes: two claimers lease the same job, claim query updates rows outside the CTE limit, or locking queries use inconsistent ordering.
Related user stories: 4, 5, 6

P4. Postgres catch-up is per-run partitioned
User-visible promise: `read_events_since(run_id, after_event_id)` uses a run-local event cursor backed by per-run sequence allocation and `previous_event_id`.
Representative prompts or actions: write interleaved events for two runs; each run starts at event id 1 and catches up independently.
Public boundary: supervisor_event_ledger.
Allowed outcomes: per-run `event_id` and `previous_event_id`; global id/timestamp remain audit metadata only.
Forbidden outcomes: requiring a single global gapless event id for per-run catch-up.
Related user stories: 7, 8

P5. Migration and production entrypoints are explicit
User-visible promise: The Postgres lane has an Alembic migration and a `make migrate` target, while PgBouncer usage is represented in config/docs.
Representative prompts or actions: `make migrate POSTGRES_DSN=postgresql://...`.
Public boundary: target_config_load.
Allowed outcomes: migration applies cleanly to Postgres; SQLite users are unaffected.
Forbidden outcomes: hidden schema drift, missing migration files, or production workers bypassing the pool guidance.
Related user stories: 2, 9, 10

## Implementation Decisions

- Keep SQLite as the default `State` implementation for filesystem paths.
- Add a Postgres implementation selected only when `state_db` is a Postgres URL.
- Keep the dispatcher interface stable and add a batch claim method for Postgres multi-claimer use.
- Use a partial unique index for non-terminal idempotency tokens; NULL tokens remain allowed.
- Use a materialized CTE as the Postgres claim boundary so the limit is evaluated before update.
- Use the same `ORDER BY priority, created_at, id` in every Postgres locking claim query.
- Store Postgres event stream ids per run with `previous_event_id`; retain a global serial id only as audit metadata.
- Add Alembic files and a `make migrate` target. The migration lane is Postgres-only.
- Postgres connections should be aimed at a PgBouncer transaction-pool DSN in production; local tests may connect directly to a test database.

## Testing Decisions

- First RED tests use `dual_agent_runner` and `supervisor_event_ledger` public boundaries.
- Live Postgres tests require an explicit `CODEX_SUPERVISOR_POSTGRES_TEST_DSN` and skip otherwise.
- SQL-shape tests always run and assert the SKIP LOCKED CTE fence, limit placement, and uniform order.
- SQLite rollback-lane tests remain part of the ordinary full suite.
- No test may call live models, workers, MCP transports, or external agents.

## Out of Scope

- Temporal, Restate, DBOS, or another runtime.
- Postgres sharding or multi-region replication.
- Replacing the SQLite default.
- Changing reviewer panel or gate semantics.
- Reworking the whole supervisor state store beyond the event/job lane required for this layer.

## Further Notes

This layer is intentionally the multi-writer database foundation. A later layer may add richer dispatcher topology, admission control policy, or runtime orchestration, but this PRD is about correctness under Postgres concurrency.
