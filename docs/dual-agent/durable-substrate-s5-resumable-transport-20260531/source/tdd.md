# TDD Plan: Durable Substrate S5

## test_catch_up_dual_agent_workflow_returns_cursor_page

Maps to: P1, Slice 1

RED: Append several workflow events, call the public supervisor tool with a
stored cursor, and observe there is no tool-level way to read the ledger tail.

GREEN: `catch_up_dual_agent_workflow` returns only events after the cursor,
ascending by `event_id`, with payloads and `next_event_id` set to the highest
delivered id. An empty tail returns an empty list and does not move the cursor.

## test_codex_supervisor_mcp_exposes_catch_up_tool

Maps to: P1, P5, Slice 1

RED: Build the FastMCP server and observe the catch-up tool is absent.

GREEN: The server registers `catch_up_dual_agent_workflow` alongside submit,
poll, and resume prompt tools.

## test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome

Maps to: P1, P2, P3, Slice 2

RED: Simulate a client dropping after detached submit. Append events and
complete the job while disconnected. On reconnect, observe that the client has
to restart or misses events because no resync tool exists.

GREEN: Re-submit with the same `client_token` returns the same job with
`reattached=true`, the fake launcher is called once, catch-up returns all missed
events exactly once and then no duplicates from the advanced cursor, and poll
returns the ledger terminal outcome even after `result.json` is deleted.

## test_reconnect_protocol_doc_is_present

Maps to: P4, Slice 3

RED: Search for durable S5 reconnect documentation and find no protocol
covering persisted client state, idempotent re-submit, catch-up cursor
advancement, and ledger terminal poll.

GREEN: `docs/dual-agent/durable-substrate-s5-resumable-transport-20260531/reconnect-protocol.md`
documents the app-level resync protocol and the non-contiguous event id rule.

## Existing Regression Tests

Maps to: P5, Slice 4

RED: The new tool breaks existing S1 event-tail behavior, S2 idempotent submit,
S3a ledger-first poll, or MCP tool registration.

GREEN: Focused workflow-driver and MCP stdio tests pass, `git diff --check`
passes, and the full suite remains green.

## Regression Commands

- `uv run pytest tests/test_dual_agent_workflow_driver.py tests/test_codex_supervisor_mcp_stdio.py -q`
- `uv run --extra dev pytest -q`
