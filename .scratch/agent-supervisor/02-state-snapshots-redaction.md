# Issue 02: Persist Run Snapshots, Scope Contracts, Tail Offsets, and Redaction

## What to build

Extend state storage so each run has an immutable snapshot, a scope contract,
persistent tail offsets, hook request audit rows, action rows, decision labels,
and redacted payload storage. Enable SQLite WAL mode.

## PRD promise

**Promise IDs:** P5, P6

**User-visible promise:** Every run captures frozen inputs for replay, and
secrets are redacted before persistence or Telegram delivery.

**Public boundary:** `run_registration_api`, `event_ingestion_api`,
`redaction_pipeline`

**Allowed outcomes:** snapshot is written once; replay uses stored scope; daemon
restart resumes from persisted offset; never-touch patterns are present; stored
and sent payloads contain redaction markers.

**Forbidden outcomes:** live config rewrites old scope; daemon restart
duplicates already-ingested events; protected path writes are benign; secrets
appear in SQLite or Telegram text.

**Representative prompt/action:** Register a run with protected paths, ingest
events containing `Authorization: Bearer token`, restart state, and inspect
stored rows.

## Acceptance criteria

- [ ] Schema includes `run_snapshots`, `hook_requests`, `actions`,
      `decision_labels`, and `tail_offsets`.
- [ ] SQLite starts in WAL mode.
- [ ] Scope contracts are frozen after run creation.
- [ ] Tail offsets persist across `State` reinitialization.
- [ ] Redaction is applied before writing event-like payloads, verdicts,
      actions, hook requests, and Telegram message text.

## TDD plan

First public behavior: registering a run writes one immutable run snapshot with
the supplied scope contract and built-in never-touch patterns.

RED: Add a `run_registration_api` test that registers a run, attempts to update
the same run with different scope, and asserts the snapshot remains unchanged.

GREEN: Add schema migration and snapshot write-once behavior.

Next cycles:

- RED/GREEN for `tail_offsets` persistence across state restart.
- RED/GREEN for `redaction_pipeline` against events, hook requests, verdicts,
  actions, and Telegram-bound text.
- RED/GREEN for WAL mode.

## Blocked by

None - can start immediately.
