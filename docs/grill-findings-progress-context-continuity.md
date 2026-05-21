# Grill Findings — Progress Context Continuity

Source PRD: `docs/prd/claude-supervisor-telegram-prd.md`

## Findings

### PCC-G1 — Outbound messages are part of the conversation too

Status: resolved in issue 11.

Finding: CS7 preserved user turns and model answers, but CS8 progress
notifications bypassed that memory path. That made the Telegram chat feel like
it forgot the message it had just sent.

Resolution: successful watched-run progress notifications are recorded as
completed supervisor notification turns.

### PCC-G2 — Progress memory must not grant action authority

Status: resolved in issue 11.

Finding: Remembering "PR merged" or "run shipped" helps answer suggestions, but
must not imply permission to steer or launch the next worker.

Resolution: progress rows are read-only continuity. Mutations still go through
`action_executor` and approval policy.

### PCC-G3 — First test must span progress send plus chat follow-up

Status: resolved in issue 11.

Finding: A helper-only test for "record notification" would not catch the real
bug: the next Telegram turn did not see the notification.

Resolution: the first RED streams a watched event, then invokes
`TelegramChatSupervisor.handle_message` and inspects the runtime context.
