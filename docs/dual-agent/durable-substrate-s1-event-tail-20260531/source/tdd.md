# Durable Substrate S1 Event Tail TDD Plan

## Public Boundary

Use `supervisor.state.State`. This is the correct first boundary because the
transport recovery layer will depend on a durable state read, and the current
gap is the absence of `WHERE event_id > ?` below all consumers.

## Test Cases

### test_read_events_since_returns_ascending_tail_after_cursor

Maps to: ISS-1, P1
RED: `State` has no `read_events_since`, so consumers can only call
`recent_events`.
GREEN: Add `read_events_since` and assert it returns only the requested run's
events with `event_id > cursor`, ascending, capped by `limit`, with decoded
payload and cursor id.

### test_read_events_since_starts_from_beginning_and_empty_tail

Maps to: ISS-1, P1
RED: No start-from-zero cursor read exists.
GREEN: `after_event_id=0` and `None` return from the first event; a cursor at
the latest event returns `[]`.

### test_read_events_since_tolerates_non_contiguous_event_ids

Maps to: ISS-1, P4
RED: A gap fixture cannot be tailed safely without range semantics.
GREEN: Delete or otherwise skip an intermediate committed event id and assert
`event_id > cursor ORDER BY event_id ASC` still returns later rows.

### test_events_run_event_index_exists_and_serves_tail_query

Maps to: ISS-2, P2
RED: The schema only has `idx_events_run ON events(run_id, ts)`, and
`EXPLAIN QUERY PLAN` cannot show the new index.
GREEN: Add `idx_events_run_event ON events(run_id, event_id)` and assert the
tail query plan uses it without a full scan.

### test_state_constructor_adds_tail_index_to_existing_database

Maps to: ISS-2, P2
RED: An old database opened through `State` lacks the `(run_id, event_id)`
index.
GREEN: `State(...)` schema bootstrap creates the missing index idempotently on
open.

### test_event_tail_consumer_catches_up_after_disconnect_once

Maps to: ISS-3, P3, P4
RED: A consumer with only a stored cursor cannot catch up all disconnected
events deterministically.
GREEN: A consumer stores the max delivered `event_id`, disconnects, events are
appended, reconnect reads all events after the stored cursor exactly once, and
the next poll returns `[]`.

### test_existing_event_reads_keep_behavior

Maps to: ISS-4, P5
RED: Adding the new query changes existing snapshot/gate read behavior.
GREEN: `recent_events` still returns a bounded ascending page with its existing
flattened shape, and `read_dual_agent_gate_events` remains ascending.

## RED/GREEN Plan

RED: Add state-level tail-read tests and observe missing method/index failures.
GREEN: Add the schema index and `read_events_since`.

RED: Add query-plan and old-database bootstrap tests.
GREEN: Ensure the schema bootstrap includes `CREATE INDEX IF NOT EXISTS` for
the new index and keep the existing `(run_id, ts)` index.

RED: Add reconnect consumer proof.
GREEN: Return `event_id` and decoded payload so the consumer can persist the
largest observed cursor.

RED: Run focused state tests, then full suite.
GREEN: Keep `recent_events`, dual-agent gate reads, and replay/export behavior
unchanged.
