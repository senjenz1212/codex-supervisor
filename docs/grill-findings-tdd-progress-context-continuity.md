# TDD Grill Findings — Progress Context Continuity

Source issue: `.scratch/claude-supervisor-telegram/11-progress-context-continuity.md`

## Findings

### PCC-T1 — Preserve origin in the persisted row

Status: resolved in the TDD plan.

Finding: If progress notifications are stored as normal user turns, Claude may
mistake them for Sam's words.

Resolution: store request metadata with `origin=progress_notification` and
`kind=watched_run_progress`.

### PCC-T2 — Record only after successful Telegram send

Status: resolved in the TDD plan.

Finding: If Telegram delivery fails but memory records the notification, the
supervisor creates a conversation state Sam never saw.

Resolution: persist the notification only after `notifier.send_message`
succeeds.

### PCC-T3 — Keep the current user message out of prior context

Status: resolved in the TDD plan.

Finding: The regression test should not pass by accidentally echoing the
current "What's ure suggestion?" message into `recent_turns`.

Resolution: reuse the existing `exclude_turn_id` behavior and assert the
current message is absent from prior context.
