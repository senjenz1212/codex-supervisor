# 07 — Conversation Continuity

Status: implemented.

## PRD Promise

- CS7 — Telegram Conversations Feel Continuous

## Public Boundary

First RED: `telegram_chat_ingress`

Secondary boundary: `supervisor_tool_api`

## Representative Prompt

1. "monitor Vela chat bot"
2. Later: "what changed?" or "use the same PR"

The second turn must receive a redacted continuity pack containing recent
Telegram turns and conversation metadata.

## Allowed Outcomes

- The current Telegram message is persisted.
- Claude runtime receives recent prior supervisor turns for that `chat_id`.
- The current message is excluded from prior-turn memory.
- A returned Claude SDK `session_id` is stored on the conversation row.
- If a previous SDK session id exists, the next runtime can resume it.
- Claude can call `read_supervisor_turns` for bounded traceback.

## Forbidden Outcomes

- Daemon restart erases conversation state.
- Claude sees unredacted prior Telegram turns.
- The current user message is duplicated as prior memory.
- Remembered approvals or stale context execute actions directly.
- Replay or traceback requires live Telegram or live model calls.

## TDD Plan

1. RED: `telegram_chat_ingress` passes previous turns + previous SDK session id
   to runtime and updates the stored SDK session id after the response.
2. RED: `telegram_chat_ingress` excludes the current running turn from
   `recent_turns`.
3. RED: `supervisor_tool_api.read_supervisor_turns` returns bounded, redacted
   prior turns.
4. GREEN: add `supervisor_conversations`, state APIs, continuity-context
   builder, runtime plumbing, MCP tool exposure.
5. GREEN: refresh `summary` and `active_run_id` every 10 completed Telegram
   turns through a fail-soft summarizer.
6. REFACTOR: keep destructive action authority outside memory; continuity is
   read-only context only.

## Verification

- `tests/test_telegram_chat_ingress.py`
- `tests/test_supervisor_tool_api.py`
- `tests/test_connector_registry.py`
