# TDD Grill Findings — Desktop Progress Status Sync

Source issue: `.scratch/claude-supervisor-telegram/13-desktop-progress-status-sync.md`

## Findings

### DPSS-T1 — First RED must cross the progress streamer

Status: resolved in the TDD plan.

Finding: Testing a helper that formats a status item would not prove watched
rollout progress is mirrored to Desktop.

Resolution: the first test calls `TelegramProgressStreamer.handle_event` and
asserts the fake target adapter receives `append_status_item`.

### DPSS-T2 — Failure path needs its own assertion

Status: resolved in the TDD plan.

Finding: The live proxy failure was previously hidden behind an empty
`app_server_error`; Desktop sync needs auditable failed action rows.

Resolution: tests assert adapter failure records `actions.status='failed'` and
Telegram still sends.

### DPSS-T3 — GUI repaint is a HITL observation

Status: resolved in the TDD plan.

Finding: Unit tests can prove the app-server request shape, but cannot prove
the Electron renderer repainted.

Resolution: the implementation reports adapter results and documents live
smoke status separately.
