# Grill Findings — Progress Context Backfill

Source PRD: `docs/prd/claude-supervisor-telegram-prd.md`

## Findings

### PCB-G1 — Repair must not pretend to be delivery

Status: resolved in issue 12.

Finding: Backfilling conversation memory is not the same as sending Telegram.
If the command resends the notification, Sam sees duplicate progress and may
trust a stale state as new activity.

Resolution: the backfill path never calls a notifier and returns
`sent_telegram=false`.

### PCB-G2 — Blank chat ids are worse than no repair

Status: resolved in issue 12.

Finding: The live bug was made confusing by a manual repair row written under
an empty chat id. That row exists in SQLite but is invisible to the real
conversation.

Resolution: blank chat ids raise before any write. The shell wrapper sources
the same `secrets.env` as the daemon so config expansion sees the real chat id.

### PCB-G3 — Backfill must be idempotent

Status: resolved in issue 12.

Finding: Operators will retry repair commands while debugging. Duplicate
notification rows would pollute the continuity pack and make Claude overweight
one event.

Resolution: the repair checks for an existing chat/run/event notification row
and returns `already_present`.
