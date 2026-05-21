# Issue 11: Progress Context Continuity

## What to build

Persist successful watched-run progress notifications as supervisor-visible
conversation context. When Sam replies to a progress notification with a vague
follow-up such as "what's your suggestion?", the Claude supervisor must receive
the just-sent progress notification in its continuity pack.

This closes the bug where Telegram showed "Run complete ... 18c.5 shipped" but
the next supervisor answer behaved as if it only knew an older mid-turn steer.

## PRD Promise

**Promise IDs:** CS11

**Public boundary:** `telegram_progress_context`

**Representative action:**

1. `TelegramProgressStreamer` sends:

   ```text
   Run complete
   run=run_019e2964...
   event=37207

   PR #57 is now merged. 18c.5 is now shipped.
   ```

2. Sam sends:

   ```text
   What's ure suggestion?
   ```

3. `TelegramChatSupervisor` passes the run-complete notification in
   `conversation_context.recent_turns` before invoking the runtime.

## Allowed outcomes

- The notification is redacted and persisted after Telegram send succeeds.
- The persisted row is clearly marked as a supervisor progress notification,
  not as a user prompt.
- The conversation `active_run_id` is updated to the watched run.
- Follow-up prompt context includes the progress notification and excludes the
  current user message from prior memory.
- This is read-only memory. It does not create approval or steering authority.

## Forbidden outcomes

- Progress notifications are sent to Telegram but invisible to the next
  supervisor turn.
- Raw rollout payloads, secrets, or Telegram tokens are persisted or sent to
  Claude.
- A stale rolling summary wins over a newer progress notification.
- Progress context causes steering, approval, kill, restart, or merge without
  the normal action ledger.

## TDD Plan

1. **RED — integration boundary.** Create a watched run, stream a
   `task_complete` event through `TelegramProgressStreamer`, then call
   `TelegramChatSupervisor.handle_message("What's ure suggestion?")` with a
   fake runtime. Assert the runtime receives a recent turn whose response text
   contains the run-complete notification and "18c.5 is now shipped."
2. **GREEN — persist outbound notification.** Add a state API for completed
   supervisor notification turns, and call it after progress send succeeds.
3. **RED/GREEN — redaction + active run.** Assert secrets are redacted in the
   persisted notification and conversation `active_run_id` is set to the run.
4. **VERIFY.** Run the Telegram ingress/progress tests and the full suite.

## Status

Implemented and verified.

## Verification

- `tests/test_telegram_progress_context.py`
- `tests/test_telegram_progress_streaming.py`
- `tests/test_telegram_chat_ingress.py`
- Full suite: `172 passed`
