# S5 Reconnect Protocol

Durable-substrate S5 uses app-level resync over the existing stdio MCP tools.
It does not require Streamable HTTP, an MCP EventStore, or `Last-Event-ID`.

## Client State To Persist

- `run_id`
- `job_id` when known
- stable `client_token`
- canonical detached submit payload
- `last_event_id` after each processed event page

Use a stable `client_token` for reconnect. If a client omits it, submit derives
an idempotency key from the request payload, so changing any logical payload
field can create a second job.

## Reconnect Sequence

1. Submit with `submit_dual_agent_workflow_job`.
2. Process returned events or progress notifications and persist
   `last_event_id` after processing them.
3. If the MCP connection drops, reconnect and call
   `submit_dual_agent_workflow_job` again with the same `client_token` and
   logical request. The existing S2 idempotency path returns the same `job_id`
   and does not launch another worker.
4. Call `catch_up_dual_agent_workflow(run_id, last_event_id)`.
5. Process returned events in ascending `event_id` order.
6. Persist the returned `next_event_id` after the page is processed.
7. Repeat catch-up with the new cursor until a page returns no events.
8. Resume `poll_dual_agent_workflow_job(job_id)`.

If the job completed during the disconnect, poll reads the terminal outcome from
the ledger. `result.json` is only a compatibility cache.

## Cursor Rules

- `event_id` is monotonic but not contiguous.
- Never request `last_event_id + 1`.
- Always request events where `event_id > last_event_id`.
- Advance to the maximum delivered `event_id`, exposed as `next_event_id`.
- Empty pages preserve the caller cursor.
- `has_more` is a conservative hint. Continue until a page returns no events.

## Server Guarantees

- Catch-up is read-only and does not append progress events.
- Returned pages are ordered by `event_id ASC`.
- Returned events are redacted by the supervisor tool boundary.
- Reattach safety comes from S2 idempotent submit.
- Terminal recovery comes from S3a ledger-first poll.
