# PRD Grill Findings: Detached Dispatcher And AXI CLI

### Finding G1: Poll latency alone is insufficient

Status: resolved

Risk: A test could prove poll is fast while still allowing an in-process dispatcher call on a lucky fast path. Resolution: the PRD and tests require monkeypatching dispatcher construction to raise, and poll must still return the durable row.

### Finding G2: AXI cannot become a second truth layer

Status: resolved

Risk: A CLI with its own state files would recreate the filesystem-truth problem. Resolution: AXI uses `CodexSupervisorMcpAPI` and `State`; every command reads or writes the same ledger records as MCP.

### Finding G3: Operator output needs parseable recovery hints

Status: resolved

Risk: A friendly CLI that omits catch-up or dispatcher hints would not improve transport recovery. Resolution: every command includes `help[]` lines, JSON output, and explicit next commands for poll, catch-up, and dispatcher launch.

### Finding G4: Dispatcher ownership must remain testable

Status: resolved

Risk: Removing poll-side spawn without testing dispatcher handoff could strand jobs. Resolution: tests cover AXI submit followed by targeted dispatcher claim, request write, worker spawn, and ledger poll of the spawned row.

### Finding G5: Receipt sanitization must apply at the new boundary

Status: resolved

Risk: CLI submit could bypass the runtime-evidence provenance hardening. Resolution: AXI submit calls the same API merge path and tests forged supervisor receipts are downgraded before persistence.

### Finding G6: Launchd documentation must name the actual command

Status: resolved

Risk: Operators could keep relying on MCP as an executor if the daemon path is ambiguous. Resolution: docs name `codex-supervisor-workflow-dispatcher`, the plist location, and the responsibility split.
