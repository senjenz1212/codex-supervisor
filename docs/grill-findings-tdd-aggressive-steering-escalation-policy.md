# CS21 TDD Grill Findings — Aggressive Steering Escalation Policy

## Finding 1 — The first proof must hit the execution boundary

A config-loader test would not prove the supervisor can actually deliver a
steer automatically.

Resolution: the first RED targets `action_executor.execute_actions_async` with
fake target and Telegram dependencies, asserting auto-delivery for
`inject_steering`.

## Finding 2 — The Telegram path must be tested too

The user experiences this through Telegram, not by calling the executor
directly.

Resolution: `test_telegram_steer_request_auto_delivers_in_enforce_mode` drives
`telegram_chat_ingress` through a fake Claude runtime that calls
`request_steering_async`.

## Finding 3 — Approval mode must not regress

Adding enforce behavior could accidentally make advise mode auto-deliver too.

Resolution: the existing approval-path tests remain in the focused suite:
`test_telegram_steer_request_creates_approval_action_not_direct_injection` and
`test_supervisor_tool_api_request_steering_creates_telegram_approval`.

## Finding 4 — Destructive actions need an adversarial test

An allowlist bug could turn `auto_execute_actions={"kill"}` into a live
destructive action.

Resolution: `test_auto_execute_never_bypasses_destructive_approval` explicitly
passes `kill` in the auto-execute set and proves it still creates a pending
approval row and does not call the adapter.
