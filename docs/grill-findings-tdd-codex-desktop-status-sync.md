# TDD Grill Findings — Codex Desktop Status Sync

Source issue: `.scratch/claude-supervisor-telegram/10-codex-desktop-status-sync.md`

## Findings

### DSS-T1 — Assert the method name, not only a delivered result

Status: resolved in the TDD plan.

Finding: A fake adapter could return `delivered=true` while still using resume
or another user-turn command under the hood.

Resolution: tests parse the JSON-RPC lines written to app-server stdin and
assert the exact `thread/inject_items` method.

### DSS-T2 — Redaction must happen before the app-server write

Status: resolved in the TDD plan.

Finding: Redacting only the returned action result still leaks secrets into
Codex thread history.

Resolution: tests inspect the fake app-server request body and assert the raw
secret does not appear in the injected item.

### DSS-T3 — App-server failure should not break approvals or Telegram

Status: resolved in the TDD plan.

Finding: The supervisor already has a history of failed steering when the Codex
binary is not on LaunchAgent PATH. Status sync must fail closed and explain the
reason instead of raising.

Resolution: tests cover launch/protocol failure returning `delivered=false`;
the adapter result is durable action payload material, not an exception.
