# CS18 — Desktop IPC Role Discovery

## Product Promise

The supervisor can enumerate Codex Desktop IPC `client-discovery-request` roles
without claiming capabilities it cannot actually serve. Discovery output is
safe to audit: method names, protocol versions, source client ids, and
parameter keys only; no parameter values are stored.

## Public Boundary

`codex_desktop_ipc_role_discovery`

Replay captured IPC frames through `summarize_client_discovery_capture(path)`
and exercise `CodexDesktopIpcClient(... request_handlers=...)` against a fake
framed socket.

## Allowed Outcomes

- Discovery frames summarize `method`, `version`, `source_client_id`, and
  sorted `param_keys`.
- Default observer mode answers `canHandle=false`.
- `canHandle=true` is advertised only for methods with an explicit handler.
- If a request is forwarded to a registered handler, the client responds with a
  framed `response` instead of letting the router time out.

## Forbidden Outcomes

- Store discovery parameter values in summaries or logs.
- Reply `canHandle=true` without an explicit handler.
- Start or steer a Codex turn during role discovery.
- Let a forwarded request hang silently.

## TDD Plan

1. RED: replay a capture with one `client-discovery-request`; summary must
   include method/version/source/param keys and exclude secret-like values.
2. GREEN: add `summarize_client_discovery_request` and
   `summarize_client_discovery_capture`.
3. RED: default observer answers `canHandle=false` and records the sanitized
   discovery summary.
4. GREEN: store discovery summaries and keep default false.
5. RED: explicit handler gates `canHandle=true`, and a forwarded request
   receives a response without `turn/start` or `turn/steer`.
6. GREEN: add handler-gated request responses.
7. Verify: run focused IPC tests and a short live false-observer enumeration
   against the real Desktop socket.

## Grill Findings

### G1 — `canHandle=true` is a promise to answer the next request

Finding: If the observer advertises capability and then fails to answer the
forwarded request, it can make the Desktop router wait for a timeout.

Resolution: `canHandle=true` is derived only from registered handlers. Unknown
methods remain `false`.

### G2 — Discovery params can contain sensitive values

Finding: `ide-context` includes `workspaceRoot`, and future discovery params
could include more sensitive context.

Resolution: summaries store only parameter keys, never values.

### G3 — Role discovery is not steering

Finding: The same IPC connection can speak request/response frames, which makes
it tempting to test `turn/start` early.

Resolution: CS18 has no `turn/start`/`turn/steer` live probe. It only enumerates
roles and handler safety.

## Live Probe Evidence — 2026-05-20

### Passive Role Enumeration

False-observer mode against the real Desktop IPC socket observed one discovery
role in a short capture:

- method: `ide-context`
- version: `0`
- source client: `e90d0cb3-bc86-4a47-800e-daa15c036a87`
- parameter keys: `workspaceRoot`

No parameter values were stored.

### Handler-Gated `ide-context`

When the observer registered an explicit `ide-context` handler:

- Desktop sent `client-discovery-request(method=ide-context)`.
- The observer answered `canHandle=true`.
- Desktop then forwarded an actual `request(method=ide-context)` to the
  observer.
- The observer returned a framed error response:
  `unsupported-by-supervisor-observer-probe`.

This proves `canHandle=true` is a real routing promise, not a harmless label.

### Two-Client `thread/read` Routing Probe

To avoid claiming Desktop behavior, two supervisor clients were attached:

- Listener client registered a `thread/read` handler.
- Source client sent `thread/read` for Vela session
  `019e2964-42b5-7ef3-95dc-8d6714482724`.
- Router sent `client-discovery-request` to the listener.
- Listener answered `canHandle=true`.
- Router forwarded `request(method=thread/read)` to the listener.
- Source received the listener's explicit error:
  `supervisor-probe-thread-read-handler`.

Conclusion: the IPC router can route `thread/read` when some attached client
advertises that method. The earlier `no-client-found` means no existing Desktop
client advertised `thread/read` to this external source, not that framing was
wrong.
