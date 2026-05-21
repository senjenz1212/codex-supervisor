# Issue 12: Progress Context Backfill

## What to build

Add an operator repair path for watched-run progress notifications that were
already sent to Telegram before outbound notifications were persisted into
`supervisor_turns`.

The repair must not send Telegram again. Its only job is to write the same
redacted progress notification into the supervisor conversation so the next
Telegram follow-up has continuity.

## PRD Promise

**Promise IDs:** CS12

**Public boundary:** `progress_context_backfill`

**Representative action:**

```bash
scripts/backfill-progress-context.sh \
  run_019e2964-42b5-7ef3-95dc-8d6714482724 \
  37207
```

## Allowed outcomes

- Requires a non-empty chat id, from config or explicit CLI argument.
- Reads the stored rollout event by `run_id` and `event_id`.
- Formats the same redacted progress message as live watched-run streaming.
- Persists one completed supervisor notification turn with
  `origin=progress_notification_backfill`.
- Updates `supervisor_conversations.active_run_id`.
- Re-running the command returns `already_present` instead of creating a
  duplicate row.

## Forbidden outcomes

- Sends a second Telegram notification.
- Writes a row with blank `chat_id`.
- Creates duplicate progress context rows for the same chat/run/event.
- Stores raw secrets from the rollout payload.
- Creates steering, approval, kill, restart, or merge authority.

## TDD Plan

1. **RED — public repair boundary.** Write a stored `task_complete` event, call
   `backfill_progress_context`, then assert the next recent turn for that chat
   contains `Run complete` and `18c.5 is now shipped`.
2. **GREEN — repair module.** Add `supervisor.progress_backfill` plus a shell
   wrapper that sources the same secrets as the daemon.
3. **RED/GREEN — fail closed.** Blank chat ids raise and do not write a
   `supervisor_turns` row.
4. **RED/GREEN — redaction.** Secret-like event text is redacted in the stored
   turn.
5. **VERIFY.** Run the new tests, CS11 progress context tests, Telegram
   progress tests, and the full suite.

## Status

Implemented and verified.
