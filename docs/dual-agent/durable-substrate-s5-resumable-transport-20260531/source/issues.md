# Issues: Durable Substrate S5

## Slice 1: Expose Workflow Catch-Up Tool

Priority: P0

Scope: Add `catch_up_dual_agent_workflow(run_id, last_event_id, limit)` to the
Codex supervisor API and FastMCP tool registration. The tool must call
`State.read_events_since` and return a redacted cursor page.

Acceptance Criteria:

- [ ] Events returned all have `event_id > last_event_id`.
- [ ] Events are ascending and include payloads.
- [ ] `next_event_id` advances to the highest returned event id.
- [ ] Empty pages return `events=[]` and preserve the caller cursor.
- [ ] The tool is registered by `codex-supervisor-mcp`.

PRD promise: P1, P5

## Slice 2: Integration Reconnect Scenario

Priority: P0

Scope: Add a public-boundary test that simulates a client dropping after submit,
events being appended while disconnected, reconnect re-submit using S2, catch-up
through the new tool, and terminal poll through S3a.

Acceptance Criteria:

- [ ] Re-submit with the same client token returns the same `job_id`.
- [ ] The fake worker launcher is called once.
- [ ] Catch-up returns all missed events exactly once in ascending order.
- [ ] A second catch-up from `next_event_id` returns no duplicates.
- [ ] Poll returns the ledger terminal outcome after `result.json` is deleted.

PRD promise: P1, P2, P3

## Slice 3: Reconnect Protocol Documentation

Priority: P0

Scope: Add a durable S5 protocol document that tells clients which state to
persist and which tools to call on reconnect.

Acceptance Criteria:

- [ ] The document names persisted `run_id`, `job_id` or reattach request,
  `client_token`, and `last_event_id`.
- [ ] The document gives the reconnect sequence:
  idempotent submit, catch-up, cursor advancement, poll.
- [ ] The document states event ids are monotonic but not contiguous.
- [ ] The document states catch-up is read-only and ledger events are the replay
  source.

PRD promise: P4

## Slice 4: Regression Guard

Priority: P1

Scope: Keep existing S1/S2/S3a behavior and the broader workflow suite green.

Acceptance Criteria:

- [ ] Existing event tail, idempotent submit, and ledger terminal outcome tests
  keep passing.
- [ ] `git diff --check` passes.
- [ ] `uv run --extra dev pytest -q` passes.

PRD promise: P5
