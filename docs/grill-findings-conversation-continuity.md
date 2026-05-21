# Grill Findings — Conversation Continuity

Source PRD: `docs/prd/claude-supervisor-telegram-prd.md`

## Findings

### CC-G1 — Do not confuse Claude SDK resume with supervisor memory

Status: resolved in issue 07.

Finding: SDK session resume helps the conversation feel continuous, but it is
not the auditable source of truth. The supervisor must persist its own
conversation row and raw turn log in SQLite.

Resolution: `supervisor_conversations` stores the current SDK session id,
rolling summary, active run id, and turn count. `supervisor_turns` remains the
raw redacted audit log.

### CC-G2 — First RED must hit Telegram ingress, not a helper

Status: resolved in issue 07.

Finding: The customer-visible promise is "I can say 'what changed?' and the
bot remembers context." A helper-only test would miss the actual conversation
boundary.

Resolution: issue 07 starts with `telegram_chat_ingress`, asserting the runtime
receives previous turns and the conversation record updates after the response.

### CC-G3 — Continuity must not grant action authority

Status: resolved in issue 07.

Finding: Remembering "the same PR" or "Vela" must not let Claude execute stale
approval or destructive actions from memory.

Resolution: continuity context is read-only. Existing `action_executor` and
mode-policy checks remain the only mutation path.
