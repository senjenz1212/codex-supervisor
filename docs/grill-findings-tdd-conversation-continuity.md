# TDD Grill Findings — Conversation Continuity

Source issue: `.scratch/claude-supervisor-telegram/07-conversation-continuity.md`

## Findings

### CC-T1 — The boundary test must prove memory is injected before Claude answers

Status: resolved.

Finding: Persisting turns after the response is already covered. The missing
behavior is whether the next Claude turn can see prior context.

Resolution: first RED test asserts the fake runtime receives a
`conversation_context` containing the previous Telegram turn and previous Claude
session id.

### CC-T2 — The current user message must not be duplicated as prior memory

Status: resolved.

Finding: If the current message is stored before context construction, naive
history queries can include it as a previous turn.

Resolution: context builder receives `exclude_turn_id` and tests assert the
current message is absent from `recent_turns`.

### CC-T3 — MCP traceback should be available separately from prompt injection

Status: resolved.

Finding: Claude should be able to ask for deeper history without receiving all
history every turn.

Resolution: `read_supervisor_turns` is added to the supervisor MCP tool API as a
bounded, redacted traceback tool.
