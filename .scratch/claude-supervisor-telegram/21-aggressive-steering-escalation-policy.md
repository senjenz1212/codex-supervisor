# CS21 — Aggressive Steering Escalation Policy

## PRD Promise

CS21 — Aggressive Steering Proceeds Only Within Escalation Policy.

When `modes.steering_injection` is `enforce`, the supervisor may automatically
deliver non-destructive steering such as "continue", "re-anchor", or "do the
suggested next low-risk step" through the action ledger. It must ping Sam for
destructive recovery, ambiguous authority, blocked delivery, or anything
outside the allowlist.

## Public Boundaries

- `action_executor`
- `supervisor_tool_api`
- `telegram_chat_ingress`

## Allowed Outcomes

- `steering_injection: advise` preserves the existing Telegram approval flow.
- `steering_injection: enforce` may auto-deliver `inject_steering`.
- Auto-delivery writes a durable action row with `auto_executed=true`,
  `requires_approval=false`, and adapter result metadata.
- Duplicate steering in one batch is deduped.
- Kill, restart, and destructive recovery still require approval or remain
  unavailable.

## Forbidden Outcomes

- Auto-steering without an action row.
- `advise` mode auto-delivers steering.
- `enforce` mode auto-executes kill, restart, or destructive recovery.
- Stale Telegram callback mutates a target.
- Claude changes live modes from free text.
- A failed auto-steer is reported as delivered.

## TDD Plan

1. RED/GREEN — `action_executor` auto-delivers non-destructive steering.
   - First failing boundary test:
     `test_auto_execute_non_destructive_steering_in_enforce_mode`.
   - GREEN adds `execute_actions_async` with an explicit
     `AUTO_EXECUTABLE_ACTIONS={"inject_steering"}` allowlist.

2. RED/GREEN — destructive actions cannot be forced through auto mode.
   - Test:
     `test_auto_execute_never_bypasses_destructive_approval`.
   - Even when `auto_execute_actions={"kill"}`, adapter is not called and a
     pending approval row is created.

3. RED/GREEN — `supervisor_tool_api` exposes the enforce path.
   - Test:
     `test_supervisor_tool_api_request_steering_enforce_auto_delivers`.
   - GREEN adds `request_steering_async`; the old sync
     `request_steering` remains approval-first and returns `async_required`
     when called in enforce mode.

4. RED/GREEN — Telegram user path auto-delivers in enforce.
   - Test:
     `test_telegram_steer_request_auto_delivers_in_enforce_mode`.
   - Fake Claude runtime calls `request_steering_async`; no Telegram ask is
     created; target adapter receives one `inject_steering`.

5. Regression — advise mode still asks.
   - Existing tests:
     `test_telegram_steer_request_creates_approval_action_not_direct_injection`
     and `test_supervisor_tool_api_request_steering_creates_telegram_approval`.

## Grill Findings

PRD grill: `docs/grill-findings-aggressive-steering-escalation-policy.md`

TDD grill:
`docs/grill-findings-tdd-aggressive-steering-escalation-policy.md`

All findings are resolved in the issue plan and covered by tests.

## Implementation Notes

- `mcp_tools/supervisor_tools.py` now routes Claude's `request_steering` tool
  through `request_steering_async`, so enforce mode can auto-deliver.
- `supervisor/telegram_supervisor.py` tells Claude to trust the tool output and
  report whether a steer was delivered, queued, or failed.
- Live config is set to `steering_injection: enforce`; destructive recovery is
  still `off`.
