# Issue 06: Route Steering and Recovery Through the Action Executor

## What to build

Add an action executor that records all proposed actions, deduplicates steering
per session, asks for Telegram approval when required, expires stale approvals,
and routes target operations through the selected target adapter.

## PRD promise

**Promise IDs:** P4, P8

**User-visible promise:** Steering, kill, restart, and recovery are mode-aware,
audited, target-adapter routed, and destructive actions require fresh approval.

**Public boundary:** `action_executor`, `mode_policy`

**Allowed outcomes:** one pending injection per session; stale approvals expire;
target adapter returns delivered or degraded result; destructive actions fail
closed when Telegram is unavailable.

**Forbidden outcomes:** direct subprocess calls outside target adapters;
duplicate steering injections race; old Telegram callback performs a new action;
crash recovery kills a process without approval.

**Representative prompt/action:** Simulate hard divergence, approve a re-anchor
from Telegram, and assert one target steering action is delivered.

## Acceptance criteria

- [ ] `actions` rows are created for proposed and executed actions.
- [ ] Steering calls selected target adapter, not Codex-specific subprocess code.
- [ ] One pending steering action per target session is enforced.
- [ ] Telegram approvals include nonce and expiry.
- [ ] Kill and restart require approval in every mode.
- [ ] Telegram outage fails closed for destructive actions.

## TDD plan

First public behavior: a hard-divergence steering action in enforce mode creates
one pending action, waits for valid approval when required, and calls the fake
target adapter exactly once.

RED: Add an `action_executor` test with fake Telegram and fake target adapter.
Submit duplicate steering actions for the same session and assert only one
delivers after a valid non-expired approval.

GREEN: Implement action ledger, dedupe, approval check, and adapter dispatch.

Next cycles:

- RED/GREEN for stale callback rejection.
- RED/GREEN for Telegram outage fail-closed on kill/restart.
- RED/GREEN for advise mode creating recommendations but no target action.

## Blocked by

- `.scratch/agent-supervisor/01-target-adapter-foundation.md`
- `.scratch/agent-supervisor/02-state-snapshots-redaction.md`
