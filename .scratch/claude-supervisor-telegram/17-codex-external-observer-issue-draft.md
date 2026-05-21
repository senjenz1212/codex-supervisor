# Draft Codex Issue — Document External Desktop Observer Attach

## Title

Document app-server/Desktop IPC observer attach for external tools

## Body

I'm building a local supervisor that observes Codex Desktop sessions and sends
approval-gated steering through supported surfaces. I can now reproduce a
specific app-server/Desktop IPC behavior that looks like an undocumented
observer attach gap.

Environment:

- macOS
- Codex Desktop `26.513.31313`
- Codex CLI `0.131.0-alpha.9`
- Desktop IPC socket:
  `/var/folders/.../T/codex-ipc/ipc-501.sock`

What works:

1. The Desktop IPC socket is reachable.
2. It uses 4-byte little-endian length-prefixed JSON frames.
3. A framed initialize request succeeds:

```json
{
  "type": "request",
  "requestId": "...",
  "sourceClientId": "codex-supervisor-probe",
  "version": 1,
  "method": "initialize",
  "params": { "clientType": "CODEX_SUPERVISOR" }
}
```

Response:

```json
{
  "type": "response",
  "resultType": "success",
  "method": "initialize",
  "result": { "clientId": "..." }
}
```

4. After initialize, the observer receives `thread-stream-state-changed`
   broadcasts for loaded Desktop sessions, including snapshots for active
   conversations.
5. The Desktop sends `client-discovery-request` frames to the observer. In a
   short capture, I observed:

```json
{
  "type": "client-discovery-request",
  "request": {
    "method": "ide-context",
    "version": 0,
    "params": { "workspaceRoot": "..." }
  }
}
```

If my observer replies `canHandle=true` for `ide-context`, Desktop forwards an
actual `request(method=ide-context)` to my client. So `canHandle=true` is a real
routing promise.

What does not work:

Direct `thread/*` requests from the external client return `no-client-found`,
for example:

```json
{
  "type": "request",
  "requestId": "...",
  "sourceClientId": "<initialized external client id>",
  "version": 1,
  "method": "thread/read",
  "params": {
    "threadId": "<existing Desktop-owned thread id>",
    "includeTurns": false
  }
}
```

Response:

```json
{
  "type": "response",
  "requestId": "...",
  "resultType": "error",
  "error": "no-client-found"
}
```

Additional routing probe:

I attached two external supervisor clients. Client A registered a fake
`thread/read` handler; Client B sent `thread/read`. The router asked Client A
via `client-discovery-request`, Client A replied `canHandle=true`, the router
forwarded the `thread/read` request to Client A, and Client B received Client
A's explicit error response.

So the router can route `thread/read` when some attached client advertises the
method. The `no-client-found` result appears to mean that no current Desktop
client advertises `thread/read` to this external source, not that the framing is
wrong.

Fixture-only `turn/steer` routing probe:

To avoid mutating a real Desktop session, I also tested `turn/steer` against a
fake framed router with two external supervisor clients. Client A registered a
fake `turn/steer` handler; Client B sent `turn/steer`; the router performed the
same `client-discovery-request` / `canHandle=true` / forwarded request flow and
returned Client A's explicit error response to Client B.

So `turn/steer` appears to use the same capability-routing mechanics. I did not
send live `turn/steer` to a real Desktop session.

Question:

Is there a documented `clientType`, handshake field, target-client discovery
flow, method role, or observer/subscriber role that allows an external tool to:

- observe loaded Desktop thread state,
- subscribe to thread stream updates,
- request thread metadata/read state through the existing Desktop/app-server
  owner without falsely advertising itself as the `thread/read` handler, and
- optionally submit an approval-gated `turn/start` or `turn/steer` through a
  documented Desktop/app-server handler for the loaded thread?

Related asks appear close to this:

- configurable/exposed app-server endpoint
- remote/network transport for attaching external tools

The concrete gap is: framed IPC initialize works, observer broadcasts are
visible, and client-discovery routing works, but no Desktop/app-server client
appears to advertise `thread/read` or documented steering handlers for an
external observer source. If the intended answer is "broadcast-only observer
clients are supported but request forwarding is not," it would be helpful to
document that explicitly.
