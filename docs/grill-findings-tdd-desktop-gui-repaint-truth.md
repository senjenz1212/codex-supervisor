# TDD Grill Findings — Desktop GUI Repaint Truth

## T1 — First RED must hit the streamer's public boundary

Finding: A helper-only classifier test would not prove that live progress
actions are audited correctly.

Resolution: The first RED streams a watched event through
`TelegramProgressStreamer` and inspects the persisted `append_status_item`
action.

## T2 — Health wording must be pinned too

Finding: Even if action rows are corrected, `/health` can still imply the
feature is live Desktop sync because it only echoes the mode.

Resolution: Add a health test for `desktop_status_sync_effective`.

## T3 — Keep a future verified path open

Finding: Hard-coding all delivered writes as `history_only` would block a
future app-server transport that can prove GUI repaint.

Resolution: Add a positive `desktop_gui_repaint="verified"` test that maps to
`gui_live`.
