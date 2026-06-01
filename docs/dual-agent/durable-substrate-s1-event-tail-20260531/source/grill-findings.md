# Durable Substrate S1 Event Tail PRD Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Could this accidentally turn the event log into the sole source of
truth?
resolution: The PRD explicitly limits S1 to append-only progress/replay reads.
Verdicts, actions, hook requests, and workflow tables remain authoritative
writes. Production consumer migration is out of scope.

### Finding G2

status: resolved
severity: high
question: Does the cursor logic rely on contiguous event ids?
resolution: The PRD requires `event_id > after_event_id ORDER BY event_id ASC`
and a non-contiguous fixture. Tests must simulate gaps and prove no arithmetic
cursor assumptions.

### Finding G3

status: resolved
severity: high
question: Will an existing supervisor database receive the new index?
resolution: The index is added to the idempotent schema bootstrap with
`CREATE INDEX IF NOT EXISTS`, and tests must open an old-style database and
assert the index exists plus the query plan uses it.

### Finding G4

status: resolved
severity: medium
question: Could adding the new read break existing snapshot reads?
resolution: The slice adds a new method only. `recent_events` and
`read_dual_agent_gate_events` remain unchanged, with regression coverage
included in the TDD plan.

### Finding G5

status: waived
severity: medium
question: Should S1 migrate Telegram progress or the MCP transport layer now?
reason: The user explicitly scoped those migrations to later S4/S5 slices.
This slice proves the durable substrate that later consumers will adopt.

## Decision

No open PRD grill findings remain.
