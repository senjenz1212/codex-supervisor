# CS16 — Codex Desktop IPC Refresh Probe

## Product Promise

When the supervisor investigates Codex Desktop GUI reflection, it uses the same
local Desktop IPC family that the Electron app uses, rather than unsafe
coordinate clicking or invisible CLI resume. Any result stays honest: a
successful IPC write or broadcast is not reported as visible GUI repaint unless
a separate visible verification signal proves it.

## Public Boundary

`codex_desktop_ipc_client`

Call `CodexDesktopIpcClient.initialize()`, `.request(...)`, and
`.broadcast_query_cache_invalidate(...)` against a framed fake Desktop IPC
socket.

## Allowed Outcomes

- Framed IPC initialize succeeds and returns a Desktop-assigned client id.
- Diagnostic requests surface `no-client-found` as an error result instead of
  hiding it as a successful thread read.
- Cache invalidation broadcasts may be sent as a safe repaint probe.
- Broadcast refresh results are classified as `desktop_gui_repaint=unverified`.

## Forbidden Outcomes

- Newline JSON-RPC is used against the Desktop IPC socket.
- A refresh probe starts or steers a Codex turn.
- A broadcast is reported as visible GUI repaint without human-visible or
  renderer-observed verification.
- Coordinate GUI automation is used as part of this path.

## TDD Plan

1. RED: `CodexDesktopIpcClient.initialize()` uses 4-byte little-endian framed
   JSON and registers `clientType=CODEX_SUPERVISOR`.
2. RED: `request("thread/read", ...)` preserves a `no-client-found` response
   so operators can see that direct read routing is not proven.
3. RED: `broadcast_query_cache_invalidate(["recent-conversations"])` sends a
   framed broadcast, never `turn/start` or `turn/steer`, and returns
   `desktop_gui_repaint=unverified`.
4. GREEN: add the narrow framed IPC client.
5. Verify: run the new boundary tests, the existing app-server/Desktop tests,
   and a live read-only handshake against the actual Desktop IPC socket.

## Grill Findings

### G1 — Do not treat socket access as GUI reflection

Finding: The live Desktop socket accepts framed `initialize`, but direct
`thread/read`, `thread/list`, and `thread/loaded/list` return
`no-client-found`. Socket access alone is not enough to claim Desktop-visible
state sync.

Resolution: The client exposes those errors directly and classifies cache
invalidation broadcasts as `unverified`.

### G2 — Broadcast refresh is a probe, not a delivery transport

Finding: `query-cache-invalidate` has no response and may only invalidate React
Query keys; it may not update the in-memory active conversation store.

Resolution: The CS16 implementation does not wire this into live progress by
default and does not mark GUI repaint as verified.

### G3 — Avoid coordinate automation regression

Finding: Coordinate-based GUI steering previously landed in the wrong Desktop
chat.

Resolution: CS16 uses only socket frames. It does not click, type, or submit
prompts.
