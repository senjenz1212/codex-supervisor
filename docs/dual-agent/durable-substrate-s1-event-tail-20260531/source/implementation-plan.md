# Durable Substrate S1 Event Tail Implementation Plan

## Scope Summary

Implement durable-substrate S1 only: add a state-layer incremental event tail
reader and a supporting SQLite index. The reader is a substrate primitive for
future reconnect/catch-up work; this slice deliberately does not migrate
Telegram progress, MCP submit/poll, or terminal result storage.

## Files / Modules To Touch

- `supervisor/state.py`
- `tests/test_state_event_ledger.py`

## Modules And Public Boundaries

- `supervisor.state.State`: schema bootstrap, append-only event writer, and
  new generic cursor tail read.
- `SCHEMA`: idempotent SQLite DDL that runs on every `State(...)` open and
  therefore upgrades existing local ledgers without a separate data migration.
- `tests/test_state_event_ledger.py`: public-boundary tests for state event
  append/read behavior. Tests may inspect SQLite query plans because P2 is an
  index contract.

## Steps

1. Add `CREATE INDEX IF NOT EXISTS idx_events_run_event ON events(run_id, event_id);`
   immediately after the existing event index in `SCHEMA`.
2. Add `State.read_events_since(run_id, after_event_id, limit)` near
   `recent_events`.
3. Return dictionaries with `event_id`, `ts`, `source`, `kind`, and decoded
   `payload`.
4. Add tests for cursor semantics, empty tails, non-contiguous ids, query-plan
   index use, old database bootstrap, reconnect exact-once catch-up, and
   unchanged existing reads.
5. Run focused state tests, then the full dev suite.

## Detailed Implementation

### `supervisor/state.py`

- Keep the existing line:
  `CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, ts);`
- Add:
  `CREATE INDEX IF NOT EXISTS idx_events_run_event ON events(run_id, event_id);`
- Add a method:

```python
def read_events_since(
    self,
    run_id: str,
    after_event_id: int | None = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ...
```

- Query shape:

```sql
SELECT event_id, ts, source, kind, payload_json
FROM events
WHERE run_id=? AND event_id > ?
ORDER BY event_id ASC
LIMIT ?
```

- Decode `payload_json` once per row and return:

```python
{
    "event_id": row["event_id"],
    "ts": row["ts"],
    "source": row["source"],
    "kind": row["kind"],
    "payload": decoded_payload,
}
```

- Treat `after_event_id is None` as `0`.
- Return `[]` for `limit <= 0`.
- Do not flatten payload fields the way `recent_events` does; this new API is
  a cursor source and should keep envelope fields separate from the stored
  event payload.

### `tests/test_state_event_ledger.py`

Add tests at the existing state event ledger boundary:

- `test_read_events_since_returns_ascending_tail_after_cursor`
- `test_read_events_since_starts_from_beginning_and_empty_tail`
- `test_read_events_since_tolerates_non_contiguous_event_ids`
- `test_events_run_event_index_exists_and_serves_tail_query`
- `test_state_constructor_adds_tail_index_to_existing_database`
- `test_event_tail_consumer_catches_up_after_disconnect_once`
- `test_existing_event_reads_keep_behavior`

The query-plan assertion should require an indexed search and no full scan. It
may assert the new index name when SQLite reports it, but the safety invariant
is "index-served range search for run_id plus event_id cursor," not brittle
formatting of the whole query-plan string.

## Risks

- R1. SQLite query-plan output is version-sensitive. Mitigation: assert no
  `SCAN events` and prefer detecting `idx_events_run_event` or an indexed
  `SEARCH events` detail rather than matching the full plan text.

- R2. New API shape could be confused with `recent_events`. Mitigation: keep
  `recent_events` unchanged and add a regression test that asserts its
  flattened `id` shape still works separately from the new `payload`-nested
  tail read.

- R3. Cursor code might accidentally assume contiguous ids. Mitigation: add a
  non-contiguous fixture by deleting an intermediate committed event and then
  tailing with `event_id > cursor`.

- R4. Existing databases might not receive the index. Mitigation: create an
  old-style SQLite fixture with `events` and `idx_events_run` only, open it
  through `State`, and assert `idx_events_run_event` exists.

- R5. Overreach into later durable-substrate slices. Mitigation: do not modify
  `telegram_progress.py`, MCP submit/poll/job code, verdict/action/hook
  tables, or workflow result storage in this slice.

## Traceability

| PRD Promise | Implementation point | TDD proof |
|---|---|---|
| P1 generic event-tail read | `State.read_events_since` query with `event_id > ? ORDER BY event_id ASC LIMIT ?` | `test_read_events_since_returns_ascending_tail_after_cursor`; `test_read_events_since_starts_from_beginning_and_empty_tail` |
| P2 index-served tail query | `idx_events_run_event ON events(run_id, event_id)` in `SCHEMA` | `test_events_run_event_index_exists_and_serves_tail_query`; `test_state_constructor_adds_tail_index_to_existing_database` |
| P3 explicit writer/reader boundary | `write_event` remains append writer; `read_events_since` is the catch-up read | `test_event_tail_consumer_catches_up_after_disconnect_once` |
| P4 non-contiguous event ids | Range cursor, no arithmetic paging | `test_read_events_since_tolerates_non_contiguous_event_ids` |
| P5 existing reads unchanged | No edits to `recent_events` or gate read query semantics | `test_existing_event_reads_keep_behavior`; existing replay/artifact tests |

## Verification Plan

1. `uv run pytest tests/test_state_event_ledger.py -q`
2. `uv run pytest tests/test_supervisor_tool_api.py tests/test_telegram_progress_streaming.py -q`
3. `uv run pytest tests/test_dual_agent_artifacts.py -q`
4. `uv run --extra dev pytest -q`

## Non-Goals Checklist

- [ ] Do not change `telegram_progress.py`.
- [ ] Do not change MCP submit/poll/job semantics.
- [ ] Do not change terminal outcome storage.
- [ ] Do not rebuild projections from events.
- [ ] Do not remove or alter `idx_events_run ON events(run_id, ts)`.
