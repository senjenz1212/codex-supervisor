# CS19 — Fixture-Only `turn/steer` Routing Probe

## Product Promise

The supervisor can learn whether Codex Desktop IPC routes `turn/steer` by
handler advertisement without writing to a real Desktop session. Mutating
methods are blocked by default; route probes require an explicit opt-in and a
fake router.

## Public Boundary

`codex_desktop_ipc_turn_steer_route_probe`

Call `CodexDesktopIpcClient.request("turn/steer", ...)` against a fake framed
router only.

## Allowed Outcomes

- `turn/start`, `turn/steer`, and `turn/interrupt` raise
  `CodexDesktopIpcError` by default.
- A fixture can pass `allow_mutating_methods=True` to test routing semantics
  without touching the real Desktop socket.
- If a fake listener advertises a `turn/steer` handler, the fake router forwards
  the request and the source receives the handler response.

## Forbidden Outcomes

- A live Desktop IPC socket receives `turn/steer` during this slice.
- A route probe runs without explicit mutating-method opt-in.
- A listener advertises `canHandle=true` and fails to answer the forwarded
  request.
- Any test asserts visible GUI reflection.

## TDD Plan

1. RED: direct `turn/steer` request should fail before opening/sending on the
   socket unless `allow_mutating_methods=True`.
2. GREEN: add mutating-method guard to `CodexDesktopIpcClient.request`.
3. RED: fake two-client router proves `turn/steer` routes to an advertised
   handler and returns that handler's response.
4. GREEN: use existing discovery/forwarded-request handler machinery through
   the fake router; no production live-send path added.
5. Verify: focused IPC tests, full suite, compileall.

## Grill Findings

### G1 — `turn/steer` probing is itself mutating if it hits Desktop

Finding: The method name is the desired future steering path, but a live probe
could move a real session.

Resolution: CS19 does not run live `turn/steer`. The client now blocks
mutating methods by default.

### G2 — Route semantics can be proven without a real session

Finding: CS18 showed the router forwards methods to any client that advertises
a handler.

Resolution: CS19 uses a fake framed router with two supervisor clients to prove
`turn/steer` follows the same routing mechanics.

### G3 — GUI reflection is still not proven

Finding: Routing a method to a fake handler says nothing about Desktop renderer
state.

Resolution: CS19 records no `desktop_gui_repaint=verified` outcome and does not
change live config.

## Post-Slice Live Probe With HITL Opt-In

After CS19 landed, HITL explicitly approved trying the live Desktop path against
the Vela Slack bot thread:

- thread id: `019e2964-42b5-7ef3-95dc-8d6714482724`
- socket: default Codex Desktop IPC socket
- Codex CLI: `codex-cli 0.132.0`

Result:

- `initialize` succeeded.
- `turn/steer` returned `resultType=error`, `error=no-client-found`.
- No steering text was delivered by this IPC path.

Follow-up passive status probe:

- `append_status_item` through app-server `thread/inject_items` returned
  `delivered=true`, `reason=app_server_injected`.
- The marker was persisted in the Vela rollout JSONL.
- The live Desktop IPC observer saw the Vela thread snapshot but no
  post-injection stream patch in the probe window.
- A direct screenshot of the Codex window did not show the appended marker in
  the visible chat area.

Conclusion: live `turn/steer` is not currently usable from the external
supervisor client because no Desktop-side handler is advertised. Passive
history append still works as history-only. GUI reflection remains unverified.
