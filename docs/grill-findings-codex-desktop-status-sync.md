# Grill Findings — Codex Desktop Status Sync

Source PRD: `docs/prd/claude-supervisor-telegram-prd.md`

## Findings

### DSS-G1 — Passive status must not be steering in disguise

Status: resolved in CS10 and issue 10.

Finding: Using `codex exec resume` would make Desktop history move, but it
starts a new turn and changes the target agent's work queue. That violates the
user-visible promise of "reflect the state" rather than "tell Codex to do
something."

Resolution: the public action is `append_status_item`, implemented through
app-server `thread/inject_items`. `inject_steering` remains a separate action.

### DSS-G2 — Desktop repaint is a separate claim from thread-history write

Status: resolved in CS10 and issue 10.

Finding: A stdio app-server process can write to the same Codex thread history,
but that does not prove an already-open Desktop renderer will repaint
immediately.

Resolution: action results must distinguish `delivered` from
`desktop_gui_repaint`. The v2 implementation reports repaint as `unverified`
unless a future control-socket or live subscription proof verifies it.

### DSS-G3 — The first proof must hit the target adapter boundary

Status: resolved in issue 10.

Finding: Testing only a JSON-RPC helper would miss the product path used by the
action ledger and Telegram supervisor.

Resolution: the first RED test calls `CodexAdapter.execute_action` with
`TargetAction(kind="append_status_item")`; the subprocess/app-server is faked
below that boundary.

### DSS-G4 — Detached stdio is not the live Desktop path

Status: resolved in issue 10.

Finding: A detached `codex app-server --listen stdio://` process can read
archived thread metadata, but `thread/inject_items` may fail for old or
Desktop-owned sessions because the thread is not loaded in that detached
process.

Resolution: live status sync defaults to `codex app-server proxy`, which talks
to the running app-server control socket when remote control is enabled. The
stdio transport remains explicit for tests and local protocol probes.
