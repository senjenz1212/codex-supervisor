# Issue 14: Desktop GUI Repaint Truth

## PRD Promise

CS14. Desktop GUI repaint truth is not implied by history append.

## Problem

The supervisor can append passive status items into the Codex thread history
through app-server `thread/inject_items`, and those writes are visible in the
rollout JSONL. Sam's open Codex Desktop GUI does not live-render those
externally appended items. The current action ledger says `delivered`, which is
true for the history write but misleading if read as "Desktop GUI reflected."

## Public Boundary

`desktop_status_visibility_policy` plus `desktop_progress_status_sync`.

## Representative Scenario

1. Sam watches the Vela Codex Desktop session from Telegram.
2. The watched run emits `Run complete`.
3. Telegram receives the progress update.
4. Desktop status sync calls `append_status_item`.
5. The adapter returns `delivered=true`, `reason=app_server_injected`, and
   `desktop_gui_repaint=unverified`.

## Allowed Outcomes

- The action row records the app-server result.
- The action row also records `visibility.effective_state="history_only"`.
- The visibility summary says the status was appended to thread history and GUI
  live repaint is unverified.
- Telegram progress still succeeds.
- If a future adapter returns `desktop_gui_repaint=verified`, the effective
  state may be `gui_live`.

## Forbidden Outcomes

- `delivered=true` is presented as Desktop GUI-visible.
- Health/status wording says "mirrored", "reflected", or "GUI updated" when
  repaint is unverified.
- Desktop sync failure suppresses Telegram progress.
- The passive status path starts a Codex user turn.

## TDD Plan

1. RED: stream a watched run event through `TelegramProgressStreamer` with a
   fake adapter returning `delivered=true`,
   `desktop_gui_repaint="unverified"`. Assert the persisted action has
   `visibility.effective_state=="history_only"` and no GUI-live claim.
2. GREEN: add a pure visibility classifier and wire it into
   `TelegramProgressStreamer._append_desktop_status`.
3. RED/GREEN: fake `desktop_gui_repaint="verified"` and assert the classifier
   returns `gui_live`.
4. RED/GREEN: assert `/health` reports Desktop status sync as history-only
   unless GUI repaint is verified.
5. Regression: run the Desktop progress and app-server status tests, then the
   full suite.
