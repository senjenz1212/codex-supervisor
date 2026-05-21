# Grill Findings — Desktop Progress Status Sync

Source PRD: `docs/prd/claude-supervisor-telegram-prd.md`

## Findings

### DPSS-G1 — Desktop visibility must remain best-effort

Status: resolved in issue 13.

Finding: Telegram has proven live streaming. Codex Desktop GUI repaint has not.
The product promise must not silently upgrade history append into guaranteed
visible UI streaming.

Resolution: Desktop sync records adapter results and preserves
`desktop_gui_repaint=unverified` unless a live GUI smoke test proves otherwise.

### DPSS-G2 — Status sync must not become steering

Status: resolved in issue 13.

Finding: The only safe Desktop write here is passive status. Using
`codex exec resume` would start a new turn and change the session being
supervised.

Resolution: the only target action is `append_status_item`.

### DPSS-G3 — Desktop failures must not block Telegram

Status: resolved in issue 13.

Finding: If app-server proxy fails, Sam should still get the reliable Telegram
progress message.

Resolution: Desktop sync runs after successful Telegram send, records success
or failure in `actions`, and fails soft.
