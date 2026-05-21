# 08 - Telegram Progress Streaming for Watched Runs

## PRD Promise

Promise ID: CS8 - Watched Runs Stream High-Signal Progress to Telegram.

User-visible promise: after Sam watches a Codex Desktop run or named session,
the supervisor pushes concise Telegram progress updates for important rollout
events without Sam needing to ask again.

Public boundary for first RED test: `event_ingestion_api`.

Representative action: Sam watches "Vela chat bot"; Codex Desktop writes a
`task_complete` rollout event; Telegram receives one concise redacted progress
card.

Allowed outcomes:

- Watches are durable per Telegram chat and run.
- Rollout ingestion invokes the progress router after persisting the event.
- High-signal events such as `task_started`, assistant progress, and
  `task_complete` can produce a Telegram message.
- Token-count, reasoning, and low-signal tool noise are ignored.
- Duplicate event ids are not resent to the same watch.
- Send failures do not block or roll back rollout ingestion.

Forbidden outcomes:

- Every rollout line is sent to Telegram.
- Secret material or raw rollout payloads are sent to Telegram.
- Historical backfill floods the chat.
- Watching a run steers, kills, restarts, blocks, or otherwise mutates Codex.
- Daemon restart loses watch state.

## TDD Plan

Cycle 1 - RED/GREEN: public-boundary streaming.

- RED: create a durable watch, drain a rollout file through `RolloutWatcher`,
  and assert a fake Telegram notifier receives exactly one redacted message for
  a `task_complete` event.
- GREEN: add durable `run_watches` state and a `TelegramProgressStreamer`
  connected via the watcher `on_event` callback.

Cycle 2 - RED/GREEN: noise and dedupe.

- RED: feed token-count/noise events and re-deliver an already-seen event id;
  assert no Telegram send.
- GREEN: classify high-signal events only and advance each watch's
  `last_event_id` after successful send.

Cycle 3 - RED/GREEN: user-facing watch API.

- RED: call `SupervisorToolAPI.watch_run` by run id or session name and assert
  it creates a durable watch at the current event offset.
- GREEN: expose `watch_run` and `list_run_watches` through the supervisor MCP
  toolpack so Claude can make Telegram conversational watch requests.

## TDD Grill

- The first proof must enter through rollout ingestion, not through a private
  formatting helper.
- The test may fake Telegram below the notifier boundary, but must not skip run
  registration, event persistence, or watch state.
- Progress messages are notification-only. They must not create action rows,
  invoke steering, or call target adapters.
- Redaction must be asserted against the final Telegram-bound text, not only
  against SQLite.
