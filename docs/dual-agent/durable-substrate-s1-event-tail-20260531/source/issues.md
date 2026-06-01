# Durable Substrate S1 Event Tail Issues

## Slice ISS-1: Cursor Tail Read API

Type: AFK
Priority: P0
Estimate: S
Scope: Add `State.read_events_since(run_id, after_event_id, limit)` as the
generic durable event-tail reader.
PRD promise: P1, P4
First public-boundary RED test: `State.read_events_since` returns ascending
events strictly after a stored cursor and starts from the beginning when
`after_event_id` is `0` or `None`.

Acceptance Criteria:
- [ ] Only events for the requested run are returned.
- [ ] Only events with `event_id > after_event_id` are returned.
- [ ] Results are ascending by `event_id`.
- [ ] `limit` caps the result page.
- [ ] Empty tails return `[]`.
- [ ] Non-contiguous ids tail correctly.

## Slice ISS-2: Event Tail Index

Type: AFK
Priority: P0
Estimate: S
Scope: Add `idx_events_run_event ON events(run_id, event_id)` while preserving
the existing `(run_id, ts)` index.
PRD promise: P2
First public-boundary RED test: `EXPLAIN QUERY PLAN` for the tail query shows
an index search using `idx_events_run_event` instead of a full table scan.

Acceptance Criteria:
- [ ] New databases contain both `idx_events_run` and `idx_events_run_event`.
- [ ] Existing old-style databases receive `idx_events_run_event` when opened
  through `State`.
- [ ] The tail query is index-served.

## Slice ISS-3: Disconnect/Reconnect Consumer Proof

Type: AFK
Priority: P0
Estimate: S
Scope: Add a test consumer that stores the largest delivered `event_id`,
simulates a disconnect, appends events while disconnected, then reconnects and
reads the missing tail.
PRD promise: P3, P4
First public-boundary RED test: the consumer misses or duplicates events
because no cursor read exists.

Acceptance Criteria:
- [ ] Initial read advances the cursor from observed rows.
- [ ] Events appended while disconnected are delivered on reconnect.
- [ ] Each disconnected event is delivered exactly once.
- [ ] The test advances by observed `event_id`, not by `event_id + 1`.

## Slice ISS-4: Existing Read Regression Guard

Type: AFK
Priority: P1
Estimate: XS
Scope: Ensure `recent_events`, dual-agent gate reads, and replay/export
behavior are not changed by S1.
PRD promise: P5
First public-boundary RED test: existing focused state/replay/export tests
continue to pass after the new method and index are added.

Acceptance Criteria:
- [ ] `recent_events` keeps its existing shape and ascending returned page.
- [ ] `read_dual_agent_gate_events` remains ordered by ascending event id.
- [ ] Full test suite remains green.
