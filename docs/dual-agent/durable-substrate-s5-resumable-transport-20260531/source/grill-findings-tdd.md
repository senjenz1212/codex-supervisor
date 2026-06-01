# TDD Grill Findings: Durable Substrate S5

### Finding 1: First RED Must Hit The Public Tool Boundary

Status: resolved

The TDD plan starts with `catch_up_dual_agent_workflow` through the supervisor
tool API, not a helper-only test around `State.read_events_since`.

### Finding 2: The Integration Test Must Compose S1, S2, And S3a

Status: resolved

Testing catch-up alone would not prove invisible reconnect. The TDD plan now
requires the simulated drop test to re-submit idempotently, catch up events, and
poll the ledger terminal outcome after `result.json` is deleted.

### Finding 3: Duplicate Prevention Needs A Launcher Assertion

Status: resolved

The TDD plan now asserts the fake worker launcher runs once across initial
submit and reconnect submit.

### Finding 4: Cursor Advancement Needs A No-Duplicate Second Read

Status: resolved

The TDD plan now requires a second catch-up call from `next_event_id` to return
no duplicates.

### Finding 5: Documentation Needs A Test

Status: resolved

The TDD plan includes a doc-presence test so the reconnect protocol remains a
durable artifact, not just an implementation detail.

### Finding 6: Scope Guard Must Protect Existing MCP Tools

Status: resolved

The TDD plan includes MCP registration and full-suite regression checks so the
S5 tool is additive and does not weaken existing gate, submit, poll, or reviewer
behavior.
