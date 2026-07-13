# PRD Grill Findings

## Finding 1: Normalizing the DB kind could break progress callbacks

status: resolved

The durable event kind is canonical, while watcher callbacks retain the raw
`event_msg` / `response_item` kind and add `normalized_kind`. Existing Telegram
progress behavior remains covered.

## Finding 2: Editing `active_runs()` would collide with state work

status: resolved

Submission calls the existing public `State.register_run` boundary with the
workflow run ID and target session ID. The unchanged `active_runs()` query then
sees workflow runs without a union or state-schema change.

## Finding 3: Session environment identity is transport-specific

status: resolved

Resolution order is explicit parameter, `SUPERVISOR_TARGET_SESSION_ID`,
target-specific environment (`CODEX_THREAD_ID`, `CODEX_SESSION_ID`,
`CLAUDE_SESSION_ID`, `OPENCODE_SESSION_ID`), then workflow-ID fallback. AXI
exposes `--session-id`.

## Finding 4: Same-token retry could silently rewrite the join

status: resolved

After reservation, registration uses the persisted request payload from the
reserved job. A retry supplying a different target session reattaches to the
original job and original session sidecar.

## Finding 5: Scope drift and semantic drift are different evidence

status: resolved

L1 still records structured scope findings. L2/L3 and deterministic semantic
signals no longer depend on the L1 threshold, and L4 evidence lists every
signal that actually fired.
