# PRD: Durable Substrate S5 Resumable Transport

## Problem Statement

Detached dual-agent workflows can now be tailed by event cursor, submitted
idempotently, and completed with terminal outcomes in the ledger. A dropped MCP
connection still leaves clients without one documented, tool-level recovery
sequence. Operators can retry submit and poll manually, but the public
supervisor tool surface does not expose a single catch-up read that makes a
client whole again after reconnect.

S5 makes a dropped MCP connection recoverable through an app-level resync
protocol over the existing stdio MCP tools. A client persists `run_id` plus the
last delivered `event_id`; after reconnect it re-submits the same request
idempotently, reads all events after its cursor, advances that cursor from the
returned event ids, and continues polling the durable job result.

## Blocking Open Question: Transport Mechanism

Decision: choose app-level resync over the existing stdio MCP tool surface.

Evidence:

- The server entrypoint is still FastMCP stdio in
  `mcp_tools/codex_supervisor_stdio.py`, so a Streamable HTTP move would change
  the deployed transport rather than finishing the durable substrate.
- S1 already provides the required ledger cursor primitive:
  `State.read_events_since(run_id, after_event_id, limit)` uses
  `event_id > ? ORDER BY event_id ASC` and the `(run_id, event_id)` index.
- S2 already makes detached workflow submit idempotent through `client_token`
  or a derived request key, returning the existing job without launching a
  duplicate worker.
- S3a already makes terminal outcomes durable in the ledger and makes poll
  prefer the ledger over `result.json`.
- Streamable HTTP resumability would require a new transport deployment and a
  ledger-backed EventStore. That can be valuable later, but it is heavier than
  needed for the current stdio + detached job architecture.

Therefore S5 implements option A: an app-level catch-up tool plus the documented
reconnect protocol. Option B remains out of scope until there is a concrete
client or deployment requirement for Streamable HTTP and `Last-Event-ID`.

## Solution

Add a supervisor tool `catch_up_dual_agent_workflow(run_id, last_event_id,
limit)` that exposes the S1 ledger tail to MCP clients. The tool returns events
strictly after `last_event_id`, in ascending `event_id` order, capped by
`limit`, and includes the next cursor value a client should persist.

The reconnect protocol is:

1. Client starts a detached workflow with `submit_dual_agent_workflow_job` and
   stores `run_id`, `job_id`, `client_token` or stable request payload, and
   last delivered `event_id`.
2. If the MCP connection drops, the client reconnects and calls
   `submit_dual_agent_workflow_job` with the same `client_token` or same logical
   request. S2 returns the same job and does not launch a duplicate worker.
3. The client calls `catch_up_dual_agent_workflow(run_id, last_event_id)` until
   no more events are returned, processing each returned event exactly once and
   persisting `next_event_id`.
4. The client resumes polling with `poll_dual_agent_workflow_job(job_id)`.
   If the workflow completed during the disconnect, S3a returns the terminal
   outcome from the ledger even if `result.json` is unavailable.

The catch-up tool is read-only. It does not write a "catch-up happened" event,
because that would mutate the stream the client is trying to replay.

## User Stories

- As a Codex Desktop client, I can reconnect after an MCP transport drop and
  catch up on every missed workflow event without restarting the run.
- As an operator, I can retry detached submit safely and reattach to the same
  job instead of spawning duplicate Claude/Cursor work.
- As a client implementer, I have a documented protocol with the exact cursor,
  submit, catch-up, and poll calls.
- As a reviewer, I can prove the recovery path from public supervisor tools and
  ledger events without relying on chat memory or `result.json`.

## PRD Promise Contracts

P1. catch-up-returns-missed-events-exactly-once

- User-visible promise: A reconnecting client can ask for all workflow events
  after its stored event cursor and receive each missed event exactly once.
- Representative action: Store `last_event_id`, append workflow events while
  disconnected, call `catch_up_dual_agent_workflow(run_id, last_event_id)`.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Returned events all have `event_id > last_event_id`, are in
  ascending order, include payloads, and `next_event_id` equals the highest
  delivered id.
- Forbidden outcomes: Returning older events, dropping newer events, returning
  duplicates inside a page, assuming contiguous event ids, or mutating the event
  stream during catch-up.

P2. reconnect-reattaches-without-duplicate-worker

- User-visible promise: A retry after disconnect reattaches to the same detached
  workflow job and does not launch a second worker.
- Representative action: Call `submit_dual_agent_workflow_job` with a stable
  `client_token`, drop the connection, then submit again with the same token.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: The second submit returns the original `job_id`,
  `reattached=true`, and no second subprocess launch occurs.
- Forbidden outcomes: A new job id, a second worker, or a requirement that the
  client remember process-local state.

P3. terminal-outcome-survives-disconnect

- User-visible promise: If the workflow completes while the client is
  disconnected, reconnect plus poll returns the terminal result from the ledger.
- Representative action: Complete a detached job in the ledger while the client
  is disconnected, remove `result.json`, reconnect, catch up, and poll.
- Public boundary: `supervisor_tool_api`
- Allowed outcomes: Poll returns the durable terminal outcome and accepted or
  failed status from the ledger.
- Forbidden outcomes: Requiring `result.json`, reporting missing result for a
  ledger-complete job, or restarting the workflow to learn the final state.

P4. reconnect-protocol-is-documented

- User-visible promise: Client authors have a durable, repo-owned protocol doc
  for reconnect behavior.
- Representative action: Read the S5 reconnect protocol document.
- Public boundary: `docs_contract`
- Allowed outcomes: The document names the persisted state, reconnect calls,
  cursor advancement rules, and terminal poll behavior.
- Forbidden outcomes: Leaving recovery semantics implicit in tests or code
  comments only.

P5. transport-scope-stays-app-level

- User-visible promise: S5 does not rewrite the MCP transport or weaken
  dual-agent gate/reviewer semantics.
- Representative action: Run the focused S5 tests plus the full suite.
- Public boundary: `codex_supervisor_mcp`
- Allowed outcomes: Existing stdio MCP tools still register; the new catch-up
  tool is additive; gate algebra, P-probes, and reviewer behavior are
  unchanged.
- Forbidden outcomes: Streamable HTTP deployment changes, in-memory EventStore
  adoption, projection rebuilds, or altered gate/reviewer acceptance rules.

## Implementation Decisions

- Add a public API method and MCP tool named
  `catch_up_dual_agent_workflow`.
- Keep the input shape small: `run_id`, `last_event_id=0`, and `limit=100`.
- Return a redacted payload with `status`, `run_id`, `last_event_id`, `events`,
  `count`, `next_event_id`, and `has_more`.
- Compute `next_event_id` from the highest returned `event_id`, or keep the
  caller's cursor when no events are returned.
- Use `State.read_events_since` directly; do not duplicate SQL in the MCP layer.
- Use `has_more = count == limit` as a conservative paging hint. Clients keep
  calling with `next_event_id` until a page returns no events.
- Document that event ids are monotonic but not contiguous.

## Testing Decisions

- Test the catch-up tool at the supervisor tool boundary, not only the state
  helper.
- Test the full reconnect story with fake worker launch: submit, disconnect,
  append events, terminal-complete in ledger, re-submit, catch up, poll.
- Assert the launcher is called once to prove S2 reattach is used.
- Delete `result.json` before poll in the reconnect integration test to prove S3a
  ledger terminal outcome is the source of record.
- Keep S1/S2/S3a focused tests as regression coverage and run the full suite.

## Out Of Scope

- Streamable HTTP transport, MCP EventStore, and `Last-Event-ID` wiring.
- S3b event-sourced projection rebuilds.
- New gate algebra, P-probes, reviewer policy, or UI validation behavior.
- New worker durability beyond the already landed detached job substrate.
