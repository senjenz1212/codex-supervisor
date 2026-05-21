# CS22 — Quiet Mode Escalation Only

## PRD Promise

CS22 — Quiet Mode Suppresses FYIs But Preserves Escalation.

When `modes.telegram_fyis` is `off`, the supervisor should stop routine
watched-run progress and review/advice pings, while preserving progress
context and still allowing alerts, approvals, and CS21 autosteer.

## Public Boundaries

- `telegram_progress_context`
- `telegram_mcp_tools`

## Allowed Outcomes

- `telegram_fyis: advise` preserves existing progress pings.
- `telegram_fyis: off` suppresses watched-run progress Telegram sends.
- Suppressed progress is stored as quiet supervisor context.
- Watch offsets advance after suppressed progress, preventing replay spam.
- `review_updates` can still run, but MCP `send_message` suppresses
  `normal`/`fyi` messages and reports `sent=false`.
- MCP `send_message(... urgency="alert")` still sends.
- Watched-run blocker progress, including `HALTED`, sandbox blocked
  worktree creation, CI failure, and approval-needed states, is classified as
  `alert` and still sends.
- Approval prompts and destructive escalation gates remain outside FYI
  suppression.

## Forbidden Outcomes

- Quiet mode forgets progress.
- Quiet mode replays the same event later.
- Quiet mode suppresses alerts or approval prompts.
- Quiet mode suppresses watched-run blocker alerts such as `HALTED` or
  sandbox-blocked worktree creation.
- Quiet mode disables autosteer.
- A suppressed FYI reports `sent=true`.
- Routine progress or review pings still reach Telegram in quiet mode.

## TDD Plan

1. RED/GREEN — watched-run progress quiet path.
   - First failing boundary test:
     `test_quiet_telegram_fyis_suppresses_progress_ping_but_keeps_context`.
   - GREEN adds `telegram_fyi_mode` to `TelegramProgressStreamer` and wires it
     from daemon config.

2. RED/GREEN — Claude review/advice quiet path.
   - Tests:
     `test_quiet_telegram_fyis_suppresses_non_alert_mcp_messages` and
     `test_quiet_telegram_fyis_allows_alert_mcp_messages`.
   - GREEN gates Telegram MCP `send_message` by urgency when
     `cfg.modes.telegram_fyis == "off"`.

3. Regression — existing progress, context, and autosteer tests remain green.

4. Live regression — watched-run HALT bypasses quiet mode.
   - Test:
     `test_quiet_telegram_fyis_allows_halt_escalation_ping`.
   - Fixture shape mirrors the 18g launch failure: task complete with
     `HALTED before implementation` and `sandbox writable roots` in the
     last agent message.
   - GREEN adds progress urgency classification so alerts send even when
     `telegram_fyis` is `off`, while routine progress remains suppressed and
     persisted as quiet context.

## Grill Findings

PRD grill: `docs/grill-findings-quiet-mode-escalation-only.md`

TDD grill: `docs/grill-findings-tdd-quiet-mode-escalation-only.md`

All findings are resolved in the issue plan and covered by tests.
