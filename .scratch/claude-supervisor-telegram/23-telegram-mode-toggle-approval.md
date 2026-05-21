# CS23 — Telegram Mode Toggle Approval

## PRD Promise

CS23 — Telegram Mode Toggles Require Approval.

Telegram commands may request autosteer and quiet-mode changes, but the
supervisor must not mutate config or restart until Sam approves a nonce-bound
button callback.

## Public Boundary

- `telegram_chat_ingress`

## Allowed Outcomes

- `/autosteer on` requests `steering_injection: enforce`.
- `/autosteer off` requests `steering_injection: advise`.
- `/quiet on` requests `telegram_fyis: off`.
- `/quiet off` requests `telegram_fyis: advise`.
- The command creates a `mode_change` action and a Telegram ask.
- Approve updates the config and restarts the launch agent.
- Reject cancels the action and leaves config unchanged.
- Stale or spoofed callbacks fail closed through the existing nonce path.

## Forbidden Outcomes

- Free text changes live modes.
- The slash command mutates config before approval.
- A rejected callback mutates config.
- A non-allowlisted mode key can be changed from Telegram.
- Restart failure is marked as successful.

## TDD Plan

1. RED/GREEN — `/autosteer on` creates pending approval, not immediate config.
   - Test:
     `test_autosteer_command_requires_approval_before_changing_config`.
   - GREEN adds mode-command parsing and approved config application to
     `TelegramPoller`.

2. RED/GREEN — `/quiet on` reject path leaves config unchanged.
   - Test:
     `test_quiet_command_reject_does_not_change_config_or_restart`.

3. Regression — existing Telegram callback, autosteer, and quiet-mode tests
   stay green.

## Grill Findings

PRD grill: `docs/grill-findings-telegram-mode-toggle-approval.md`

TDD grill: `docs/grill-findings-tdd-telegram-mode-toggle-approval.md`

All findings are resolved in the issue plan and covered by tests.
