# CS22 PRD Grill Findings — Quiet Mode Escalation Only

## Finding 1 — Quiet cannot mean blind

Suppressing FYI pings must not drop watched-run state. Sam still needs later
questions like "what happened?" to see the suppressed progress.

Resolution: CS22 records suppressed progress as supervisor conversation
context, updates `active_run_id`, and advances the watch offset.

## Finding 2 — Escalation channels must bypass quiet mode

The product ask is "ping me only for escalations," not "never ping me."

Resolution: CS22 suppresses routine `normal` and `fyi` Telegram MCP messages,
but allows `alert` messages. Approval prompts continue through the existing
ask/action paths and are not gated by `telegram_fyis`.

## Finding 3 — Quiet mode must not disable autosteer

The aggressive mode should still proceed with low-risk steering automatically;
quiet mode only affects FYI pings.

Resolution: CS22 is bound to `modes.telegram_fyis`, while CS21 remains bound to
`modes.steering_injection`. The two modes are independent.
