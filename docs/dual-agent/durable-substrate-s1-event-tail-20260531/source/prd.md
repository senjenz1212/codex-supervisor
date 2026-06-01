# Durable Substrate S1 Event Tail PRD

## Problem Statement

The supervisor already stores durable append-only events in SQLite, but generic
readers can only ask for the last N events. `recent_events` orders by newest
first and then reverses the page, which is useful for a snapshot but not for
reconnect-and-catch-up. A consumer that has persisted `last_event_id` cannot
ask the state layer for "all events after this cursor," so MCP transport drops
still feel like lost progress instead of a resumable read.

## Solution

Add `State.read_events_since(run_id, after_event_id, limit)` as the canonical
event-tail read. It reads append-only events for one run with
`event_id > after_event_id`, returns rows in ascending `event_id` order, and
includes the `event_id` plus the decoded payload so callers can advance their
cursor. Add a `(run_id, event_id)` index at schema bootstrap time so this read
is index-served on both new and already-existing databases.

## User Stories

1. As an MCP reconnect layer, I want to resume from my last stored event id, so
   a transport drop can catch up instead of restarting a run.
2. As an event-log consumer, I want an explicit append-only reader/writer
   boundary, so I know `events` are the replay/progress log and
   `read_events_since` is the catch-up read.
3. As an operator, I want replay behavior to tolerate non-contiguous event ids,
   so deletes, rolled-back inserts, or future migration artifacts do not create
   missed or duplicated progress.
4. As a maintainer, I want the tail query index-created on existing databases,
   so old supervisor ledgers become resumable as soon as they are opened.

## PRD Promise Contracts

P1. Generic event-tail read
User-visible promise: `State.read_events_since(run_id, after_event_id, limit)`
returns only events for `run_id` with `event_id > after_event_id`, ordered by
ascending `event_id`, capped by `limit`.
Representative prompts or actions: A caller stores `after_event_id=0` for a
new connection, then polls again with the largest delivered `event_id`.
Public boundary: `supervisor.state.State`
Allowed outcomes: `None` or `0` starts from the first event; an empty tail
returns `[]`; `limit` bounds the page.
Forbidden outcomes: "last N" semantics, descending pages, events from other
runs, or flattened payloads that omit the cursor id.
Related user stories: 1, 2

P2. Tail query is index-served
User-visible promise: schema bootstrap creates
`idx_events_run_event ON events(run_id, event_id)` without removing
`idx_events_run ON events(run_id, ts)`.
Representative prompts or actions: Open a new database and an older database
that only had the previous `(run_id, ts)` index, then inspect query planning.
Public boundary: `State(...)` constructor schema bootstrap
Allowed outcomes: the new index exists, `EXPLAIN QUERY PLAN` for the tail read
uses it, and existing databases receive it idempotently on open.
Forbidden outcomes: a full events scan or migration that requires dropping
existing indexes.
Related user stories: 4

P3. Explicit writer/reader boundary
User-visible promise: `State.write_event` remains the append-only writer and
`State.read_events_since` becomes the durable cursor source for progress/replay
catch-up.
Representative prompts or actions: A test consumer reads an initial page,
stores the latest `event_id`, disconnects, events are appended while offline,
then reconnects and reads exactly the missing events.
Public boundary: `supervisor.state.State`
Allowed outcomes: every disconnected event is delivered exactly once and the
consumer advances its cursor only from observed event ids.
Forbidden outcomes: rebuilding projections from events in this slice, changing
verdict/action/hook authority tables, or requiring production consumers to
migrate before S4/S5.
Related user stories: 1, 2

P4. Non-contiguous event ids are handled
User-visible promise: consumers page by `event_id > cursor` and never by
arithmetic assumptions such as `cursor + 1`.
Representative prompts or actions: A fixture has gaps in committed event ids;
the reader asks for events after the previous observed id.
Public boundary: `State.read_events_since`
Allowed outcomes: rows after the cursor are returned even when ids skip.
Forbidden outcomes: missing events after a gap, looping forever on a gap, or
assuming ids are contiguous.
Related user stories: 3

P5. Existing reads remain unchanged
User-visible promise: `recent_events` and `read_dual_agent_gate_events` keep
their current behavior while the new tail read is added.
Representative prompts or actions: Existing tests for recent-event snapshots
and dual-agent gate transcript export still pass.
Public boundary: `State.recent_events`, `State.read_dual_agent_gate_events`,
and replay/artifact export tests.
Allowed outcomes: no behavior change to snapshot reads, transcript reads, or
deterministic replay.
Forbidden outcomes: rewriting production consumers in S1 or changing event
payload shapes for existing callers.
Related user stories: 2, 4

## Implementation Decisions

- Add the index directly to the idempotent `SCHEMA` bootstrap next to the
  existing `idx_events_run` definition.
- Return decoded payload under a `payload` field, while also exposing
  `event_id`, `ts`, `source`, and `kind` for cursor advancement and filtering.
- Treat `after_event_id=None` as `0`.
- Return `[]` for non-positive limits.
- Keep production consumers unchanged; Telegram progress and MCP resumability
  migration are later slices.

## Testing Decisions

The first RED test targets `State.read_events_since`, because S1 is a state
substrate API and the exact gap is the lack of a cursor read below transport.
Index coverage is asserted with `EXPLAIN QUERY PLAN`, and the catch-up behavior
is proven by a small consumer that disconnects, stores a cursor, and reconnects.

## Out of Scope

This slice does not make events the sole source of truth, rebuild projections,
change submit/job semantics, add idempotent submit, store terminal outcomes, or
build resumable MCP transport. It also does not migrate Telegram progress or
other production consumers to the new tail read.
