# CS21 PRD Grill Findings — Aggressive Steering Escalation Policy

## Finding 1 — "Aggressive" must be scoped to one capability

The product ask is not general autonomy. It is automatic continuation for
suggested low-risk steering while keeping escalation prompts.

Resolution: CS21 binds aggression to `modes.steering_injection=enforce` and
only to `inject_steering`. It does not change recovery actions, mode changes,
hook blocking, or Desktop status sync.

## Finding 2 — Auto-delivery still needs the action ledger

Skipping Telegram approval must not mean invisible mutation. Sam needs to be
able to audit what the supervisor sent and whether Codex accepted it.

Resolution: CS21 requires every auto-steer to create an `actions` row with
`auto_executed=true`, `requires_approval=false`, and adapter result metadata.

## Finding 3 — Destructive recovery remains outside the allowlist

"Proceed automatically" could be misread as permission to kill, restart, merge,
or expand scope.

Resolution: CS21 explicitly forbids auto-executing kill, restart, or
destructive recovery. Existing destructive approval guards remain in force.

## Finding 4 — Duplicate steering can race a target session

If Claude repeats the same suggestion or retries after a timeout, the target
could receive duplicate re-anchor prompts.

Resolution: CS21 requires deduplication for duplicate proposed steering in the
same batch and preserves pending-action deduplication for approval flows.
