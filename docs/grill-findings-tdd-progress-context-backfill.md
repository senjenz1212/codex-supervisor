# TDD Grill Findings — Progress Context Backfill

Source issue: `.scratch/claude-supervisor-telegram/12-progress-context-backfill.md`

## Findings

### PCB-T1 — Test the repair boundary, not only the formatter

Status: resolved in the TDD plan.

Finding: A formatter-only test would miss the actual failure: no usable
conversation row for the real Telegram chat.

Resolution: the first test writes a stored event, calls the repair function,
and reads `recent_supervisor_turns` for that chat.

### PCB-T2 — The test must prove no hidden action authority appears

Status: resolved in the TDD plan.

Finding: Progress memory is useful context, but it must not become a side door
for steering or approval.

Resolution: the persisted row has empty `proposed_actions`, a notification
origin, and no action-executor call path.

### PCB-T3 — The live wrapper must share daemon env semantics

Status: resolved in the TDD plan.

Finding: The failed manual backfill loaded config without `secrets.env`, so
`${TELEGRAM_CHAT_ID}` expanded to empty.

Resolution: `scripts/backfill-progress-context.sh` sources
`~/.codex-supervisor/secrets.env` before invoking the Python module.
