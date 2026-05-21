# CS17 — Decode `thread-stream-state-changed`

## Product Promise

The supervisor can investigate Codex Desktop's live renderer update path from a
replayable IPC capture before attempting any live GUI reflection. It must
distinguish "decoded Desktop stream frame" from "visible GUI repaint verified."

## Public Boundary

`codex_desktop_ipc_stream_decode`

Replay captured framed IPC messages through
`summarize_thread_stream_capture(path)` and capture live messages through
`CodexDesktopIpcClient.capture_messages(...)`.

## Allowed Outcomes

- Snapshot broadcasts decode to conversation id, host id, source client,
  protocol version, and `change_type=snapshot`.
- Patch broadcasts decode to conversation id, host id, source client, protocol
  version, `change_type=patches`, and patch count.
- Non-thread-stream messages are ignored by the decoder.
- Passive capture sends only `initialize`.
- All decoded summaries report `desktop_gui_repaint=unverified`.

## Forbidden Outcomes

- The decoder starts or steers a Codex turn.
- The decoder clicks, types, or submits through the GUI.
- A decoded snapshot/patch frame is treated as proof that Sam's visible Desktop
  chat repainted.
- `no-client-found` or missing stream frames are hidden as successful Desktop
  attachment.

## TDD Plan

1. RED: replay a JSONL capture containing client status, snapshot, and patch
   frames; only thread-stream frames should decode.
2. GREEN: add `summarize_thread_stream_state_changed` and
   `summarize_thread_stream_capture`.
3. RED: live-capture helper filters thread-stream frames while sending only
   `initialize`, never `turn/start` or `turn/steer`.
4. GREEN: add `recv_message` and `capture_messages` to the framed IPC client.
5. Verify: run the IPC boundary tests and a short passive live capture against
   the real Desktop socket. If no frames arrive, record that as "no passive
   live stream observed while idle," not as failure.

## Grill Findings

### G1 — Snapshot/patch decode is not GUI repaint proof

Finding: `thread-stream-state-changed` is the renderer-state event name, but a
decoded frame only proves the supervisor understood a message shape.

Resolution: Every decoder summary carries `desktop_gui_repaint=unverified`.

### G2 — Passive capture must not mutate target sessions

Finding: It would be tempting to trigger a turn to produce frames.

Resolution: CS17's capture path sends only `initialize`; any `turn/start` or
`turn/steer` appears in tests as a forbidden outcome.

### G3 — Missing frames are useful evidence

Finding: If a registered external client receives no stream frames while the
Desktop is idle, that still tells us the router will not replay active
conversation state to a newly attached observer by default.

Resolution: Live capture reports "no frames observed" honestly and leaves
observer attach as a Codex protocol question.

## Live Probe Evidence — 2026-05-20

Passive socket capture against
`/var/folders/kr/smjlgwxd6_l6wqjjkpw87lfh0000gn/T/codex-ipc/ipc-501.sock`
confirmed:

- `initialize` succeeds for `clientType=CODEX_SUPERVISOR`.
- Desktop broadcasts `thread-stream-state-changed` snapshots to the observer
  client.
- One observed snapshot was for the Vela session:
  `019e2964-42b5-7ef3-95dc-8d6714482724`.
- Patch frames decode path-only summaries such as `latestTokenUsageInfo` and
  `turns/72/items/49`.
- Desktop also sends `client-discovery-request` frames, for example
  `method=ide-context`; the observer now answers `canHandle=false`.
- GUI repaint remains `unverified`; these captures prove observer visibility
  into Desktop stream state, not visible renderer update.
