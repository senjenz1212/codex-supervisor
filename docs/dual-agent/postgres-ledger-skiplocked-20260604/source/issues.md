# Issues: Postgres Ledger And Workflow Job Lane

## Slice ISS-1: Add an opt-in Postgres event/job store lane

Type: AFK
Priority: P0
Estimate: M
Blocked by: None
User stories covered: 1, 2, 7, 8, 10
PRD promises covered: P1, P4
Scope: Add the Postgres event/job store module, preserve SQLite path behavior,
and expose per-run event catch-up through the existing state boundary.
First public-boundary RED test: `supervisor_event_ledger` proves Postgres writes interleaved events with per-run ids and SQLite remains the default for filesystem state paths.

Acceptance criteria:
- [ ] Filesystem `State(...)` still uses SQLite and existing event reads remain green.
- [ ] Postgres URL construction selects the Postgres lane.
- [ ] Postgres events use per-run `event_id` and `previous_event_id`; global id is audit-only.
- [ ] `read_events_since` catches up independently per run.

## Slice ISS-2: Enforce Postgres idempotency under true multi-writer submit

Type: AFK
Priority: P0
Estimate: M
Blocked by: Issue 1
User stories covered: 2, 3
PRD promises covered: P2
Scope: Add DB-enforced active-token idempotency to Postgres reservations and
prove duplicate client-token writers reattach to one durable job.
First public-boundary RED test: `dual_agent_runner` Postgres live test launches concurrent reservations with one token and observes one created job plus reattached duplicates.

Acceptance criteria:
- [ ] Postgres schema carries the non-terminal idempotency partial unique index.
- [ ] Reserve first replays existing active or terminal token rows.
- [ ] Concurrent duplicate reserve calls produce exactly one active job.
- [ ] The test proves correctness without relying on Python locks.

## Slice ISS-3: Add SKIP LOCKED multi-claimer claim path

Type: AFK
Priority: P0
Estimate: M
Blocked by: Issue 1
User stories covered: 4, 5, 6
PRD promises covered: P3
Scope: Add the Postgres batch claim query for multi-claimer dispatch, including
the materialized CTE fence, SKIP LOCKED lock acquisition, and uniform order.
First public-boundary RED test: `dual_agent_runner` Postgres live test runs concurrent claimers and verifies disjoint claims bounded by limit.

Acceptance criteria:
- [ ] Claim SQL uses a materialized CTE with `LIMIT` inside the CTE.
- [ ] CTE locks with `FOR UPDATE SKIP LOCKED`.
- [ ] All Postgres locking claim queries order by `priority, created_at, id`.
- [ ] Concurrent claimers receive disjoint jobs.

## Slice ISS-4: Add migration and production lane hooks

Type: AFK
Priority: P1
Estimate: S
Blocked by: Issues 1-3
User stories covered: 2, 9, 10
PRD promises covered: P5
Scope: Add migration/config entrypoints for the Postgres lane while documenting
PgBouncer transaction-pool usage and preserving SQLite as the default.
First public-boundary RED test: `target_config_load` / migration-shape test proves the Alembic migration and `make migrate` target exist without changing SQLite defaults.

Acceptance criteria:
- [ ] Alembic migration creates the Postgres event/job lane schema.
- [ ] `make migrate` runs the Alembic upgrade command.
- [ ] Config example documents Postgres/PgBouncer as opt-in.
- [ ] Full SQLite suite stays green.

## Coverage Index

| Promise | Covering issue(s) | Status |
|---|---|---|
| P1 | Issue 1, Issue 4 | Covered |
| P2 | Issue 2 | Covered |
| P3 | Issue 3 | Covered |
| P4 | Issue 1 | Covered |
| P5 | Issue 4 | Covered |
