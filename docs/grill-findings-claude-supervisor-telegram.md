# Claude Supervisor Telegram PRD Grill Findings

## Status

All findings are resolved in the PRD, issue pack, and first implementation
slice.

## Findings

### CG1. "Vela chat bot" Must Not Trigger Slack Assumptions

Finding: The phrase sounds like a Slack bot, but the user's clarification makes
it a Codex Desktop session name.

Resolution: CS2 and the named-session issue explicitly forbid treating it as
Slack by default. The resolver searches local Codex runs and aliases only.

### CG2. Claude-As-Supervisor Still Needs a Tool Plane

Finding: "Let Claude Code be the supervisor" could collapse monitoring,
storage, Telegram, and action policy into one model loop.

Resolution: The PRD keeps the daemon as sensor/tool plane and exposes stable
tools to Claude. Claude reasons and converses; the daemon stores, redacts, and
enforces modes.

### CG3. Free-Text Telegram Must Not Become a Backdoor

Finding: Natural language such as "kill it" or "turn on enforce" could bypass
the existing action ledger.

Resolution: CS4 requires all mutations to become action rows and approvals.
The Telegram chat issue tests read-only answers separately from action
execution.

Follow-up closure: steering is now exposed as `request_steering`, which creates
a pending `inject_steering` action and Telegram approval prompt. Codex Desktop
receives the steer only after the callback approval path resolves the nonce.
Mode changes are not exposed in v0.7, so Telegram cannot silently flip
supervisor configuration.

### CG4. Replay Has to Include Tool Outputs, Not Just Model Text

Finding: Replaying only the final Claude text would not prove what Claude saw
or why it responded.

Resolution: CS5 and the replay issue require frozen Telegram message, tool
outputs, model output, and proposed actions.

### CG5. Session Resolution Needs Explicit Aliases

Finding: Codex rollout metadata may not always store the human-visible desktop
session title.

Resolution: Named-session resolution supports an alias registry first, then
metadata/content fallback, and ambiguity returns candidates instead of guessing.
