# Issue 05: Keep Telegram Natural-Language Actions Mode-Safe

## What to build

When Claude interprets Telegram text as a mutation request, it must produce
mode-safe proposed actions and route execution through the existing action
executor and approval path.

## PRD Promise

**Promise IDs:** CS4

**Public boundary:** `telegram_chat_ingress`, `action_executor`

**Allowed outcomes:** read-only answers are immediate; steering creates action
rows; steering delivery requires a fresh nonce-protected Telegram approval
before the target adapter receives an `inject_steering` action; free-text mode
changes do not mutate config; destructive actions require fresh approval.

**Forbidden outcomes:** free text directly kills, restarts, blocks, or injects
steering; free text changes supervisor modes; stale approval buttons execute
actions; shadow mode mutates target state.

## TDD Plan

Cycle 1 - RED/GREEN: Telegram steer request creates approval, not direct
injection.

- RED: use a fake Claude runtime at `telegram_chat_ingress` that interprets
  "steer Vela chat bot" and calls `request_steering`. Assert an
  `inject_steering` action row is `pending_approval`, a `telegram_asks` row has
  a nonce, and no target adapter call is made.
- GREEN: expose `request_steering` through `SupervisorToolAPI` and the
  supervisor MCP allowlist; route it through `action_executor.execute_actions`.

Cycle 2 - RED/GREEN: approved steering reaches Codex through the target
adapter.

- RED: submit an approved `inject_steering` action to `CodexAdapter` and assert
  it builds/runs the version-gated `codex exec resume` command via the adapter.
- GREEN: implement `CodexAdapter.execute_action` for `inject_steering` while
  keeping unsupported actions fail-closed.

## Status

Implemented for the Claude-tool path. The supervisor runtime can request
steering through `mcp__supervisor__request_steering`; execution stays behind
`action_executor` and nonce approval. Open-ended natural-language extraction
heuristics remain Claude's responsibility rather than a Python parser. Telegram
free-text mode changes remain unsupported; no `request_mode_change` tool is
exposed. CS23 adds narrow approval-gated slash commands for `/autosteer` and
`/quiet`.
