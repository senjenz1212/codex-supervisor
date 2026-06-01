# Durable Substrate S1 Event Tail TDD Grill Findings

## Findings

### Finding TG1

status: resolved
severity: high
question: Does the first RED proof hit the real missing substrate boundary?
resolution: The first test targets `State.read_events_since`, the exact state
API missing below transport recovery, rather than testing a future MCP resume
layer.

### Finding TG2

status: resolved
severity: high
question: Do tests prove no missed or duplicated events across reconnect?
resolution: The TDD plan includes a disconnect/reconnect consumer that stores
the largest delivered `event_id`, appends events offline, reads after that
cursor, and asserts exact-once delivery plus an empty subsequent poll.

### Finding TG3

status: resolved
severity: high
question: Does the plan prove index service on existing ledgers?
resolution: The plan includes both `EXPLAIN QUERY PLAN` coverage and an
old-style database opened through the current `State` constructor.

### Finding TG4

status: resolved
severity: medium
question: Could contiguity assumptions sneak into the implementation?
resolution: A non-contiguous fixture is mandatory, and the plan forbids
arithmetic paging. The cursor advances only from delivered row `event_id`
values.

### Finding TG5

status: resolved
severity: medium
question: Does this overreach into S4/S5 consumer migration?
resolution: The plan tests a local consumer proof only and leaves production
Telegram/MCP migration as a non-goal.

## Decision

No open TDD grill findings remain.
