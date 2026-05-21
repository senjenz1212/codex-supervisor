# Issue 10: Codex Desktop Status Sync Through App-Server

## What to build

Implement the v2 Desktop-visible status path for Codex targets. A supervisor
action can append a passive status item to a Codex thread through Codex
app-server `thread/inject_items`.

This is not steering. It must not start a Codex turn, run a model, or use
`codex exec resume`. It only appends a redacted, supervisor-labelled history
item so Desktop thread history can reflect supervisor-discovered state.

## PRD Promise

**Promise IDs:** CS10

**Public boundary:** `codex_app_server_status_sync`

**Representative action:**

```python
await CodexAdapter(...).execute_action(TargetAction(
    kind="append_status_item",
    session_id="019e2964-42b5-7ef3-95dc-8d6714482724",
    payload={
        "message": "Supervisor status: 18c.5 grill is approved and ready for explicit worker launch.",
        "source": "supervisor_status",
    },
))
```

## Allowed outcomes

- Adapter can connect through `codex app-server proxy` when the control socket
  is available, or launch a detached stdio app-server.
- Adapter sends JSON-RPC `initialize`.
- Adapter sends JSON-RPC `thread/resume` before injecting so archived/unloaded
  thread history is loaded into the app-server instance.
- Adapter sends JSON-RPC `thread/inject_items` with `threadId` equal to the
  target session id and a raw Responses API message item.
- The item is assistant-role, clearly labelled as supervisor status, and
  contains redacted text only.
- The action result says whether the app-server write was delivered.
- GUI repaint remains an explicit verification field, because a separate
  app-server process may update thread history without making an already-open
  Desktop view repaint immediately.

## Forbidden outcomes

- The passive status path calls `codex exec resume`, `codex resume`, or any
  user-turn/agent-turn command.
- A status append requires GUI focus, clipboard paste, AppleScript, or browser
  automation.
- Secrets appear in the injected item, returned result, SQLite action result,
  or Telegram text.
- App-server unavailable raises out of the adapter instead of returning
  `delivered=false`.
- Claude/Telgram conversation code imports app-server payloads directly.

## TDD Plan

1. **RED — public boundary status append.** Add a test that calls
   `CodexAdapter.execute_action(TargetAction(kind="append_status_item", ...))`
   with a fake app-server subprocess. Assert the subprocess receives
   `initialize`, `thread/resume`, then `thread/inject_items`, and assert no
   `codex exec resume` command is invoked.
2. **GREEN — app-server client.** Add a small JSON-RPC client and route
   `append_status_item` through it. Support both `codex app-server proxy` and
   detached `stdio://`; keep `inject_steering` unchanged.
3. **RED/GREEN — redaction and degraded state.** Add tests proving injected
   item content is redacted and that app-server launch/protocol failures return
   `delivered=false` without raising.
4. **VERIFY — local smoke.** Use the real Codex app-server binary to append a
   harmless test status into the Vela chat bot session. Report history-write
   success separately from GUI repaint.

## Status

Implemented and smoke-verified on the Vela chat bot rollout.
