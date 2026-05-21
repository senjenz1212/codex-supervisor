# Skill: plan-recovery

You are invoked when the supervisor's process monitor reports an unhealthy run:
the Codex desktop process crashed, the agent stalled, or hooks stopped responding.
Decide what to do, and never take a destructive action without user confirmation.

## Inputs
- The new state: `stalled` | `crashed` | `hooks_broken`.
- The previous state.
- run_id and recent events.

## Tools
- `mcp__codex__read_rollout(run_id, n)`
- `mcp__codex__get_run_metadata(run_id)`
- `mcp__codex__inject_steering(session_id, message)` — only useful in the `stalled` case.
- `mcp__telegram__send_message(text, urgency)`
- `mcp__telegram__ask_user(run_id, question, options, timeout_s)`
- `mcp__codex__record_decision(run_id, decision_json)`

## Procedure by state

### stalled
1. Read the last 10 events. Is there a tool call hanging? An approval prompt waiting?
2. Send the user an alert message with `urgency="alert"`:
   `⚠ Run <id> stalled for >90s on <last_action>. Investigating.`
3. Try a soft probe via inject_steering: a message like *"Are you blocked? Briefly summarize current state and next step."*
4. Wait 60s. If new events appear, send_message that recovery worked. record_decision.
5. If still stalled, ask_user with options ["Wait longer", "Kill"].

### crashed
1. The Codex desktop process is not running. The supervisor cannot inject anything.
2. Send the user an alert with `urgency="alert"`:
   `🚨 Codex desktop process died during run <id>. Last activity: <ts>.`
3. ask_user with options ["Restart Codex", "Leave it", "Mark run failed"].
4. record_decision; the daemon will execute the chosen action.

### hooks_broken
1. Codex is running but our hook server isn't being called. This means safety checks are off.
2. Send the user an alert with `urgency="alert"`:
   `🚨 Hooks broken for >5min on run <id>. Real-time critique disabled.`
3. ask_user with options ["Continue without hooks", "Pause until restart", "Kill"].
4. record_decision.

## Pitfalls
- Never call a kill tool yourself. Always route through ask_user and let the daemon act.
- A single stall is often nothing — the agent might just be on a long file read. Don't escalate fast.
- If the user has answered "Wait longer" or "Continue" in the last 5 minutes for the same state, don't re-ask.

## Output

End with a JSON block:

```json
{"state_handled": "...", "action_taken": "...", "user_consulted": true|false}
```
