# PRD Grill Findings

### Finding 1: "Behind the existing store interface" is narrower than all supervisor tables

status: resolved

Resolved: This slice covers the event ledger and workflow-job methods used by durable submit, catch-up, and dispatch. It does not port Telegram asks, actions, or run snapshots to Postgres. The PRD now names the event/job lane explicitly.

### Finding 2: A partial unique index is not enough unless reserve selects terminal rows first

status: resolved

Resolved: The PRD requires terminal-token replay before inserting a new active job. The implementation must preserve Layer 0's terminal replay behavior while using the partial unique index to close the active multi-writer race.

### Finding 3: SKIP LOCKED can still over-claim if LIMIT is not fenced before UPDATE

status: resolved

Resolved: The PRD requires a materialized CTE containing `ORDER BY`, `LIMIT`, and `FOR UPDATE SKIP LOCKED`, with the outer update joining only those rows.

### Finding 4: "Partitioned catch-up" must not break existing event consumers

status: resolved

Resolved: SQLite keeps its current global `event_id` behavior. The Postgres lane uses run-local ids under the same `read_events_since(run_id, after_event_id)` API, so consumers already carrying a run id keep the same logical contract.

### Finding 5: PgBouncer is a deployment lane, not a local test dependency

status: resolved

Resolved: The production config/documentation must point workers at a PgBouncer transaction-pool DSN. Local tests can connect directly to an explicit test Postgres DSN.
