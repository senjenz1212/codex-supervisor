# Implementation Plan: Durable Substrate S5 Resumable Transport

## Scope

Implement app-level resumable transport over the existing FastMCP stdio tool
surface. Do not change the transport, do not add Streamable HTTP/EventStore,
and do not alter dual-agent gate or reviewer semantics.

## Files / Modules To Touch

- `mcp_tools/codex_supervisor_stdio.py`
  - Add `CodexSupervisorMcpAPI.catch_up_dual_agent_workflow`.
  - Register the FastMCP tool `catch_up_dual_agent_workflow`.
  - Return redacted event pages with `next_event_id`, `count`, and `has_more`.
- `tests/test_dual_agent_workflow_driver.py`
  - Add public-boundary catch-up tests.
  - Add drop/reconnect/resume integration test composing S1, S2, and S3a.
- `tests/test_codex_supervisor_mcp_stdio.py`
  - Update MCP registration coverage to require the new catch-up tool.
- `docs/testing/public-boundaries.md`
  - Document the new MCP tool as part of the Codex supervisor public boundary.
- `docs/dual-agent/durable-substrate-s5-resumable-transport-20260531/reconnect-protocol.md`
  - Document the client reconnect protocol.
- `docs/dual-agent/durable-substrate-s5-resumable-transport-20260531/source/*`
  - Preserve PRD/TDD/issue traceability and export artifacts.

## Implementation Steps

1. Add `catch_up_dual_agent_workflow(run_id, last_event_id=0, limit=100)` on
   `CodexSupervisorMcpAPI`.
2. Delegate to `self.state.read_events_since(run_id, after_event_id=last_event_id, limit=limit)`.
3. Normalize inputs defensively:
   - `last_event_id` defaults to `0`.
   - `limit <= 0` returns an empty page.
   - event ids are treated as cursors, not arithmetic page numbers.
4. Return:
   - `status: "ok"`
   - `run_id`
   - `last_event_id`
   - `events`
   - `count`
   - `next_event_id`
   - `has_more`
5. Compute `next_event_id` from the maximum returned `event_id`; if no events
   are returned, preserve the caller cursor.
6. Keep the method read-only. Do not call `write_event` from catch-up.
7. Register the MCP tool next to submit/poll/resume prompt tools.
8. Add tests before or alongside implementation:
   - cursor page semantics at the public tool boundary;
   - gap-tolerant non-contiguous event ids through the tool;
   - read-only assertion by comparing `latest_event_id` before and after
     catch-up;
   - drop/reconnect/resume integration using fake `Popen`, S2 reattach, S1
     catch-up, and S3a ledger poll after deleting `result.json`;
   - MCP registration coverage.
9. Add the reconnect protocol document.
10. Run focused tests, `git diff --check`, then the full dev suite.

## Traceability

| PRD Promise | Issues | TDD Tests | Implementation |
|---|---|---|---|
| P1 catch-up-returns-missed-events-exactly-once | Slice 1, Slice 2 | `test_catch_up_dual_agent_workflow_returns_cursor_page`; `test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome` | `catch_up_dual_agent_workflow` delegates to `State.read_events_since` and returns cursor pages |
| P2 reconnect-reattaches-without-duplicate-worker | Slice 2 | `test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome` | Existing S2 submit idempotency path is exercised with a stable `client_token` and fake launcher count |
| P3 terminal-outcome-survives-disconnect | Slice 2 | `test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome` | Existing S3a ledger-first poll is exercised after deleting `result.json` |
| P4 reconnect-protocol-is-documented | Slice 3 | `test_reconnect_protocol_doc_is_present` | `reconnect-protocol.md` documents persisted state, reconnect sequence, cursor rules, and ledger terminal poll |
| P5 transport-scope-stays-app-level | Slice 4 | `test_codex_supervisor_mcp_exposes_catch_up_tool`; focused and full regression commands | MCP tool registration is additive; no transport rewrite or gate/reviewer change |

## Risks

- Risk: Clients interpret exactly-once as a server-side delivery guarantee.
  Mitigation: Protocol doc states the server guarantees ordered pages after the
  supplied cursor; clients must persist `next_event_id` after processing.
- Risk: Reconnect without a stable `client_token` can derive a new idempotency
  key if payload fields drift.
  Mitigation: Protocol doc makes stable `client_token` mandatory for robust
  reconnect; tests use a token and assert the launcher runs once.
- Risk: Catch-up could accidentally write an event and perturb the replay tail.
  Mitigation: Keep implementation read-only and test `latest_event_id` is
  unchanged after catch-up.
- Risk: Gap tolerance is only inherited from `State.read_events_since`.
  Mitigation: Add a public tool-boundary test that creates non-contiguous event
  ids for the target run and confirms catch-up returns the correct subset.
- Risk: Returning event payloads may leak sensitive data.
  Mitigation: Redact the catch-up response before returning, matching other MCP
  tool responses.
- Risk: Paging hint is mistaken for completeness.
  Mitigation: Document `has_more` as a conservative hint and require clients to
  continue until an empty page.

## Validation Plan

- `uv run pytest tests/test_dual_agent_workflow_driver.py tests/test_codex_supervisor_mcp_stdio.py -q`
- `git diff --check`
- `uv run --extra dev pytest -q`

## Non-Goals Guard

- No Streamable HTTP, EventStore, or `Last-Event-ID` implementation.
- No S3b projection rebuild.
- No changes to `State.read_events_since` SQL unless a test exposes a defect.
- No changes to dual-agent P-probes, reviewer algebra, Cursor policy, or
  `lead_direct` defaults.
