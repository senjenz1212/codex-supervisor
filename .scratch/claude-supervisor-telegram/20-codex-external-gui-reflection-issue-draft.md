# Draft Codex Issue — External Client GUI Reflection / Steering Handler

## Summary

Codex Desktop's framed IPC socket is reachable from an external local
supervisor client, and a read-only observer can receive the same
`thread-stream-state-changed` broadcasts that the renderer consumes. However,
external clients currently do not appear to have a documented handler/method
for GUI-visible steering or for forcing a loaded Desktop thread to reflect an
external history append.

The result is a split-brain UX for supervisor integrations:

- `codex exec resume <thread> <prompt>` can move the backend session.
- `codex app-server ... thread/inject_items` can append to thread history.
- The already-open Desktop chat does not reliably repaint or show the appended
  status/steer live.

Could Codex document or expose an external-client path for one of these?

- append a GUI-visible supervisor/status item to a loaded thread
- steer/start a turn through the same in-memory dispatcher the renderer uses
- force a loaded thread to rehydrate/reload after `thread/inject_items`

## Environment

- macOS
- Codex Desktop / CLI: `codex-cli 0.132.0`
- Local Desktop IPC socket: reachable
- External client: local supervisor process using Codex Desktop's framed
  length-prefixed IPC protocol

## Evidence

### 1. Desktop IPC initialize succeeds

An external client can connect to the local Desktop IPC socket and send the
framed `initialize` request. The Desktop returns a `clientId`.

### 2. Read-only observer receives renderer stream events

The external client receives sanitized `thread-stream-state-changed` broadcasts
for loaded threads.

Observed during a live read-only smoke:

```json
{
  "methods": ["thread-stream-state-changed"],
  "client_discovery_methods": ["ide-context"],
  "stream_change_types": ["patches", "snapshot"],
  "stream_patch_paths": ["latestTokenUsageInfo", "turns/12/items/225"],
  "desktop_gui_repaint": "unverified"
}
```

### 3. Active-turn capture shows stream patches, not callable write methods

During active Codex Desktop tool-output activity, the external observer saw
only:

- `thread-stream-state-changed`
- `client-discovery-request` for `ide-context`

It did not see externally callable methods such as `turn/start`, `turn/steer`,
`thread/read`, `thread/reload`, or `thread/hydrate`.

Diff result from sanitized captures:

```json
{
  "candidate_forced_reload_methods": [],
  "candidate_live_turn_methods": [],
  "methods_only_in_cold_start": [],
  "methods_only_in_normal_turn": [],
  "recommended_next_step": "prepare_codex_issue",
  "desktop_gui_repaint": "unverified"
}
```

Note: this diff used a snapshot-only baseline plus active-turn capture because
the same Codex Desktop app hosted the investigation session. A true cold-start
capture would require quitting/reopening Desktop.

### 4. Live `turn/steer` probe does not route

With explicit HITL approval, an external client sent `turn/steer` against a
real thread id. The IPC request returned:

```json
{
  "resultType": "error",
  "error": "no-client-found"
}
```

This suggests `turn/steer` follows capability routing, but no Desktop-side
handler is advertised to this external client.

### 5. `thread/inject_items` appends history but does not prove GUI repaint

`thread/inject_items` through app-server returned success and persisted the
status marker into the rollout JSONL, but the live Desktop observer did not see
a post-injection stream patch in the probe window, and the open Desktop chat
did not visibly show the marker.

## Repro Shape

Read-only capture:

```bash
python -m supervisor.desktop_ipc_probe capture \
  --phase normal_turn \
  --output .scratch/gui-probes/cs20-active-turn-ipc.jsonl \
  --duration-s 20 \
  --limit 120
```

Sanitized diff:

```bash
python -m supervisor.desktop_ipc_probe diff \
  --cold-start .scratch/gui-probes/cs20-snapshot-baseline-ipc.jsonl \
  --normal-turn .scratch/gui-probes/cs20-active-turn-ipc.jsonl \
  --output .scratch/gui-probes/cs20-active-turn-diff.json
```

The capture tool stores only method names, message types, client-discovery
method/param keys, thread stream change types, and patch paths. It intentionally
drops prompt text, patch values, raw params, and secrets.

## Request

Please document the intended external-client integration path for GUI-visible
thread updates. In particular:

1. Is there a supported method for an external local client to append a
   GUI-visible item to a loaded Desktop thread?
2. Is `turn/steer` intended for external clients? If so, what capability
   advertisement or handshake is required to avoid `no-client-found`?
3. Is there a supported way to ask Desktop to rehydrate/reload a loaded thread
   after an external `thread/inject_items` write?
4. If no such path exists yet, would Codex consider exposing a read-only
   observer plus GUI-visible `thread/observer/*` or `turn/external_steer`
   handler set?

The current workaround is to keep Telegram/supervisor output as the reliable
live surface and treat Desktop `thread/inject_items` as history-only.
