# Issue 13: Desktop Progress Status Sync

## What to build

Mirror high-signal watched-run progress into Codex Desktop thread history as
passive supervisor status items.

Telegram remains the live, reliable stream. Desktop sync is a best-effort
history/visibility surface: it must never start a new Codex turn, block
Telegram delivery, or claim GUI repaint unless a human-visible smoke test
confirms it.

## PRD Promise

**Promise IDs:** CS13

**Public boundary:** `desktop_progress_status_sync`

**Representative action:**

1. Sam watches `Vela Slack bot`.
2. The watched Codex session emits `Run started`, commentary, or `Run complete`.
3. `TelegramProgressStreamer` sends Telegram progress.
4. If `modes.desktop_status_sync != "off"`, the same progress message is sent
   to `CodexAdapter.execute_action(TargetAction(kind="append_status_item"))`.

## Allowed outcomes

- Desktop sync runs only for watched, high-signal progress events.
- The target action is `append_status_item`; `codex exec resume` is never used.
- The target session id comes from the registered run.
- The action is recorded in SQLite with the adapter result.
- Adapter failure records a failed action and does not suppress Telegram.
- `desktop_gui_repaint` remains `unverified` unless a live GUI smoke test
  proves otherwise.

## Forbidden outcomes

- Starting a new Codex turn to show a status message.
- Blocking or losing Telegram progress because Desktop sync failed.
- Claiming the visible Desktop GUI changed when only thread history was
  appended.
- Granting steering, approval, kill, restart, or merge authority from a status
  item.
- Replaying old already-notified events into Desktop.

## TDD Plan

1. **RED — progress boundary.** Stream a watched `task_complete` through
   `TelegramProgressStreamer` with `desktop_status_mode="advise"` and assert a
   fake target adapter receives `append_status_item` for the run's session id.
2. **GREEN — optional Desktop mirror.** Add `target_adapter` and
   `desktop_status_mode` to the progress streamer; record action rows and call
   `append_status_item`.
3. **RED/GREEN — off mode.** `desktop_status_mode="off"` must send Telegram but
   never call the target adapter.
4. **RED/GREEN — fail-soft.** Adapter failure records a failed action and does
   not block Telegram.
5. **LIVE SMOKE.** Enable app-server daemon remote control, test `proxy`, fall
   back to `stdio` if proxy does not return a JSON-RPC response, and report GUI
   repaint separately.
6. **VERIFY.** Run progress sync tests, app-server adapter tests, and the full
   suite.

## Status

Implemented. Live app-server daemon remote-control is enabled, but proxy
transport currently accepts the socket connection without returning a JSON-RPC
response; production config can use the verified `stdio` history-append path
until proxy behavior is understood.
